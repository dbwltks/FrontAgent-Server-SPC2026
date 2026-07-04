import asyncio
import base64
import logging
import time

from fastapi import HTTPException

from app.core.config import settings
from app.graph.graph_runtime import build_initial_state, get_agent_graph, graph_config_for, graph_execution_kwargs
from app.repositories.ai_usage_repo import create_usage_log_background
from app.services.agent_stream import (
    AGENT_ERROR_MESSAGE,
    AI_DISABLED_MESSAGE,
    build_session_end_payload,
    elapsed_ms_since,
    sse_event,
    stream_agent_graph_events,
)
from app.services.voice_korean_text import split_tts_segments
from app.services.voice_stt import transcribe_audio_content
from app.services.voice_tts import (
    TTS_CONTENT_TYPE,
    TTS_MAX_CONCURRENCY,
    TTS_RESPONSE_FORMAT,
    ElevenLabsWebSocketTTS,
    resolve_tts_config,
    synthesize_speech_content,
    tts_log_fields,
)

logger = logging.getLogger(__name__)


def build_voice_agent_message(transcript: str, is_barge_in: bool = False) -> str:
    """
    이전 사용자 발화/AI 답변은 checkpointer가 thread_id 기준으로 messages를
    이미 복원해주므로 여기서 다시 전달할 필요가 없다 - 끼어들기라는 사실(AI가
    말하던 도중 끊겼다는 것)만 짧게 알려주면 LLM이 히스토리를 보고 판단한다.
    """
    transcript = transcript.strip()
    if not is_barge_in:
        return transcript

    return (
        "[통화 끼어들기] 사용자가 이전 응답을 끝까지 듣지 않고 다시 말했습니다. "
        "직전 대화 맥락을 참고해 새 발화를 우선 반영하여 자연스럽게 이어서 답변하세요.\n\n"
        f"끼어든 발화: {transcript}"
    )


async def run_voice_agent_turn(
    *,
    organization_id: str,
    session_id: str,
    transcript: str,
    interrupt_context: str | None = None,
) -> dict:
    agent_graph = get_agent_graph()
    user_message = build_voice_agent_message(transcript, interrupt_context)
    return await agent_graph.ainvoke(
        build_initial_state(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            log_message=transcript,
            channel="web_call",
        ),
        config=graph_config_for(organization_id, session_id),
        **graph_execution_kwargs(),
    )


async def stream_pipeline_voice_turn_events(
    *,
    content: bytes,
    filename: str | None,
    content_type: str | None,
    organization_id: str,
    session_id: str,
    interrupt_context: str | None = None,
    ai_settings: dict,
):
    """
    Pipeline 음성 통화용 단일 스트림.

    프론트가 /voice/transcribe -> /chat -> /voice/speech를 직접 조립하지 않도록
    STT, LangGraph 실행, TTS 합성을 백엔드에서 하나의 voice 프로토콜로 묶는다.
    """

    started_at = time.perf_counter()
    stt_provider = ai_settings.get("voice_stt_provider") or settings.stt_provider
    stt_model = ai_settings.get("voice_stt_model") or settings.voice_stt_model
    tts_config = resolve_tts_config(ai_settings)
    tts_provider, tts_log_model = tts_log_fields(tts_config)

    # 클라이언트 끊김/에러 시 finally에서 정리할 수 있도록 try 밖에서 선언한다.
    tts_tasks: list[asyncio.Task] = []

    yield sse_event("turn_start", {"elapsed_ms": elapsed_ms_since(started_at)})

    try:
        transcript, upload_metadata = await transcribe_audio_content(
            content=content,
            filename=filename,
            content_type=content_type,
            model=stt_model,
            provider=stt_provider,
        )
        yield sse_event(
            "transcript",
            {
                "text": transcript,
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )

        create_usage_log_background(
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "channel": "web_call",
                "feature": "stt",
                "provider": stt_provider,
                "model": stt_model,
                "audio_bytes": len(content),
                "text_chars": len(transcript),
                "metadata": {
                    **upload_metadata,
                    "voice_pipeline_stream": True,
                },
            }
        )

        agent_graph = get_agent_graph()
        user_message = build_voice_agent_message(transcript, interrupt_context)
        initial_state = build_initial_state(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            log_message=transcript,
            channel="web_call",
        )
        config = graph_config_for(organization_id, session_id)

        final_state: dict = {}
        answer_chunks: list[str] = []

        # ElevenLabs WebSocket TTS 사용 여부
        use_ws_tts = (
            tts_config.get("provider") == "elevenlabs"
            and bool(settings.elevenlabs_api_key)
            and bool(tts_config.get("elevenlabs_voice_id"))
        )

        # 스트리밍 TTS 상태. 문장이 완성되면 백그라운드 task로 합성하고,
        # 완성된 오디오 청크는 delta 사이사이 흘려보낸다(delta 방출을 막지 않음).
        tts_buffer = ""
        audio_index = 0
        total_audio_bytes = 0
        audio_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        tts_semaphore = asyncio.Semaphore(TTS_MAX_CONCURRENCY)

        async def synth_worker(index: int, segment_text: str) -> None:
            nonlocal total_audio_bytes
            try:
                async with tts_semaphore:
                    chunk_audio = await synthesize_speech_content(
                        text=segment_text,
                        tts=tts_config,
                    )
            except Exception:
                logger.warning("streaming TTS segment failed (index=%s)", index, exc_info=True)
                return

            total_audio_bytes += len(chunk_audio)
            await audio_queue.put(
                (
                    index,
                    sse_event(
                        "audio",
                        {
                            "content_type": TTS_CONTENT_TYPE,
                            "audio_base64": base64.b64encode(chunk_audio).decode("ascii"),
                            "index": index,
                            "elapsed_ms": elapsed_ms_since(started_at),
                        },
                    ),
                )
            )

        def schedule_segment(segment_text: str) -> None:
            nonlocal audio_index
            if not segment_text.strip():
                return
            index = audio_index
            audio_index += 1
            tts_tasks.append(asyncio.create_task(synth_worker(index, segment_text)))

        pending_audio_events: dict[int, str] = {}
        next_audio_index_to_emit = 0

        def drain_ready_audio() -> list[str]:
            nonlocal next_audio_index_to_emit
            ready: list[str] = []
            while True:
                try:
                    index, event = audio_queue.get_nowait()
                    pending_audio_events[index] = event
                except asyncio.QueueEmpty:
                    break

            while next_audio_index_to_emit in pending_audio_events:
                ready.append(pending_audio_events.pop(next_audio_index_to_emit))
                next_audio_index_to_emit += 1
            return ready

        pending_conversation_messages: list[str] = []
        _ws_tts: ElevenLabsWebSocketTTS | None = None
        ws_tts_buffer = ""

        async def flush_ws_tts_at_boundary(*, flush_all: bool = False) -> None:
            nonlocal ws_tts_buffer
            if _ws_tts is None:
                return
            segments, ws_tts_buffer = split_tts_segments(ws_tts_buffer, flush_all=flush_all)
            for _ in segments:
                await _ws_tts.flush()

        async def on_delta(delta: str) -> None:
            answer_chunks.append(delta)
            nonlocal tts_buffer
            tts_buffer += delta
            if _ws_tts is not None:
                nonlocal ws_tts_buffer
                ws_tts_buffer += delta
                await _ws_tts.send_text(delta)
                segments, ws_tts_buffer = split_tts_segments(ws_tts_buffer)
                for _ in segments:
                    await _ws_tts.flush()
            else:
                segments, tts_buffer = split_tts_segments(tts_buffer)
                for segment in segments:
                    schedule_segment(segment)

        def on_node_update(node_name: str, node_state: dict) -> None:
            if node_name != "conversation":
                return
            pending_conversation_messages.append(
                sse_event(
                    "conversation_message",
                    {
                        "conversation_id": node_state.get("conversation_id"),
                        "sender_type": "customer",
                        "sender_name": "Customer",
                        "message": transcript,
                        "metadata": {
                            "session_id": session_id,
                            "channel": "web_call",
                            "source": "voice_transcript",
                        },
                        "elapsed_ms": elapsed_ms_since(started_at),
                    },
                )
            )

        if use_ws_tts:
            # ElevenLabs WebSocket TTS: LLM delta를 실시간으로 전송
            ws_ctx = ElevenLabsWebSocketTTS(tts_config)
            try:
                _ws_tts = await ws_ctx.__aenter__()
            except Exception:
                logger.warning("ElevenLabs WebSocket TTS 초기화 실패, HTTP 방식으로 폴백")
                _ws_tts = None

        async for event, data in stream_agent_graph_events(
            agent_graph=agent_graph,
            initial_state=initial_state,
            config=config,
            started_at=started_at,
            on_delta=on_delta,
            on_node_update=on_node_update,
        ):
            if event == "final_state":
                final_state = data
                continue
            yield sse_event(event, data)
            for audio_event in drain_ready_audio():
                yield audio_event
            while pending_conversation_messages:
                yield pending_conversation_messages.pop(0)

        if not final_state.get("ai_enabled", True):
            answer = AI_DISABLED_MESSAGE
            yield sse_event(
                "ai_disabled",
                {
                    "message": answer,
                    "conversation_id": final_state.get("conversation_id"),
                    "elapsed_ms": elapsed_ms_since(started_at),
                },
            )
            # AI 비활성 메시지는 delta로 흐르지 않으므로 통째로 합성한다.
            tts_buffer = answer
        else:
            answer = str(final_state.get("final_response") or "".join(answer_chunks)).strip()
            if not answer:
                raise HTTPException(status_code=502, detail="Agent response is empty")

        yield sse_event(
            "conversation_message",
            {
                "conversation_id": final_state.get("conversation_id"),
                "sender_type": "ai",
                "sender_name": "Front Agent",
                "message": answer,
                "metadata": {
                    "session_id": session_id,
                    "channel": "web_call",
                    "source": "voice_answer",
                },
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )

        if _ws_tts is not None:
            if not answer_chunks and answer:
                await _ws_tts.send_text(answer, flush=True)
            else:
                await flush_ws_tts_at_boundary(flush_all=True)
                await _ws_tts.flush()
            audio_index_ws = 0
            # iter_audio는 _receive_loop가 None을 넣을 때까지 yield하므로
            # __aexit__(종료 신호 전송 + 수신 루프 완료 대기)와 병렬로 소비한다
            async def _close_ws():
                await ws_ctx.__aexit__(None, None, None)

            close_task = asyncio.create_task(_close_ws())
            async for wav_chunk in _ws_tts.iter_audio():
                total_audio_bytes += len(wav_chunk)
                yield sse_event(
                    "audio",
                    {
                        "content_type": TTS_CONTENT_TYPE,
                        "audio_base64": base64.b64encode(wav_chunk).decode("ascii"),
                        "index": audio_index_ws,
                        "elapsed_ms": elapsed_ms_since(started_at),
                    },
                )
                audio_index_ws += 1
            await close_task
            _ws_tts = None
            audio_index = audio_index_ws
        else:
            # HTTP TTS: 남은 버퍼(마지막 문장)를 합성 예약한다.
            segments, tts_buffer = split_tts_segments(tts_buffer, flush_all=True)
            for segment in segments:
                schedule_segment(segment)

            # delta가 한 번도 흐르지 않았는데 최종 답변만 있는 경우(예: 논스트리밍 폴백) 대비.
            if audio_index == 0 and answer:
                schedule_segment(answer)

            # 남은 TTS task가 끝나기를 기다리며, 준비되는 청크를 순서대로 흘려보낸다.
            for task in asyncio.as_completed(tts_tasks):
                await task
                for audio_event in drain_ready_audio():
                    yield audio_event
        for audio_event in drain_ready_audio():
            yield audio_event

        yield sse_event(
            "audio_end",
            {
                "total_chunks": audio_index,
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )

        should_end_session = bool(final_state.get("should_end_session"))
        if should_end_session:
            yield sse_event(
                "session_end",
                build_session_end_payload(
                    organization_id=organization_id,
                    session_id=session_id,
                    conversation_id=final_state.get("conversation_id"),
                    channel="web_call",
                    started_at=started_at,
                ),
            )
            # web_call 프론트 호환: session_end와 동일 시점에 call_end도 보낸다.
            yield sse_event(
                "call_end",
                build_session_end_payload(
                    organization_id=organization_id,
                    session_id=session_id,
                    conversation_id=final_state.get("conversation_id"),
                    channel="web_call",
                    started_at=started_at,
                ),
            )

        create_usage_log_background(
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "channel": "web_call",
                "feature": "tts",
                "provider": tts_provider,
                "model": tts_log_model,
                "audio_bytes": total_audio_bytes,
                "text_chars": len(answer),
                "metadata": {
                    "voice": tts_config.get("voice"),
                    "tts_provider": tts_provider,
                    "response_format": TTS_RESPONSE_FORMAT,
                    "voice_pipeline_stream": True,
                    "audio_chunks": audio_index,
                },
            }
        )

        yield sse_event(
            "result",
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "transcript": transcript,
                "answer": answer,
                "intent": final_state.get("intent"),
                "next_action": final_state.get("next_action"),
                "task_type": final_state.get("task_type"),
                "use_knowledge": final_state.get("use_knowledge", False),
                "decision_reason": final_state.get("decision_reason"),
                "conversation_id": final_state.get("conversation_id"),
                "applied_rules": final_state.get("applied_rules", []),
                "used_knowledge": final_state.get("used_knowledge", []),
                "knowledge_context": final_state.get("knowledge_context", []),
                "should_end_session": should_end_session,
                "end_session": should_end_session,
                "end_call": should_end_session,
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )
        yield sse_event("done", {"elapsed_ms": elapsed_ms_since(started_at)})

    except HTTPException as exc:
        logger.warning("pipeline voice turn failed: status=%s detail=%s", exc.status_code, exc.detail)
        yield sse_event(
            "error",
            {
                "message": exc.detail,
                "status_code": exc.status_code,
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )
        yield sse_event("done", {"elapsed_ms": elapsed_ms_since(started_at)})
    except Exception:
        logger.exception("pipeline voice turn failed")
        yield sse_event(
            "error",
            {
                "message": AGENT_ERROR_MESSAGE,
                "status_code": 500,
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )
        yield sse_event("done", {"elapsed_ms": elapsed_ms_since(started_at)})
    finally:
        # 에러/클라이언트 끊김 시 진행 중인 TTS 합성 task를 정리한다.
        for task in tts_tasks:
            if not task.done():
                task.cancel()
