import asyncio
import base64
import hashlib
import json
import logging
import threading

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.config import settings
from app.rag.retriever import retrieve_knowledge
from app.repositories.conversation_repo import (
    create_conversation_message,
    get_or_create_conversation,
    update_conversation_last_message,
)
from app.repositories.organization_ai_settings_repo import get_ai_settings
from app.services.agent_runtime import run_agent_turn, stream_agent_turn
from app.services.agent_stream import AI_DISABLED_MESSAGE
from app.services.voice_korean_text import split_tts_segments
from app.services.voice_tts import (
    TTS_CONTENT_TYPE,
    resolve_tts_config,
    stream_speech_content,
)

router = APIRouter(prefix="/web-call", tags=["WebCall"])
logger = logging.getLogger(__name__)

CHANNEL = "web_call"
OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
REALTIME_ERROR_MESSAGE = "Realtime voice connection failed"


def _resolve_voice_mode(ai_settings: dict) -> str:
    """
    app.api.voice.get_voice_mode와 같은 판정 규칙(pipeline/realtime만 허용,
    그 외는 pipeline)이다. 거기는 organization_id로 매번 다시 DB를 조회하는
    시그니처라, 이미 가져온 ai_settings를 그대로 쓰는 web_call에서는 직접
    꺼내 쓴다.
    """
    mode = str(ai_settings.get("voice_mode") or "").strip().lower()
    return mode if mode in {"pipeline", "realtime"} else "pipeline"


async def _send(websocket: WebSocket, event_type: str, **payload) -> None:
    await websocket.send_json({"type": event_type, **payload})


class _SentenceTtsStreamer:
    """
    LLM delta가 흐르는 동안 문장이 완성될 때마다 큐에 쌓고, 문장 등장 순서대로
    하나씩 stream_speech_content를 돌며 받은 오디오 청크를 즉시 흘려보낸다.

    ElevenLabs는 진짜 스트리밍 응답을 주므로 문장 하나가 다 끝나기를 기다리지
    않고 청크가 도착하는 즉시 전송한다(첫 오디오 바이트 지연을 최소화). 문장
    간 순서는 큐로 직렬화해서 보장하되, 다음 문장 합성은 현재 문장 전송과
    동시에 진행해 빈 구간이 생기지 않게 한다.
    """

    def __init__(self, tts_config: dict, websocket: WebSocket):
        self._tts_config = tts_config
        self._websocket = websocket
        self._buffer = ""
        self._segment_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._next_index = 0
        self._worker_task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            segment_text = await self._segment_queue.get()
            if segment_text is None:
                return
            try:
                async for audio_bytes in stream_speech_content(text=segment_text, tts=self._tts_config):
                    await _send(
                        self._websocket,
                        "assistant_audio_chunk",
                        audio=base64.b64encode(audio_bytes).decode("ascii"),
                        content_type=TTS_CONTENT_TYPE,
                        index=self._next_index,
                    )
                    self._next_index += 1
            except Exception:
                logger.warning("web_call TTS segment failed", exc_info=True)

    def feed(self, delta: str) -> None:
        self._buffer += delta
        segments, self._buffer = split_tts_segments(self._buffer)
        for segment in segments:
            if segment.strip():
                self._segment_queue.put_nowait(segment)

    def flush(self, fallback_text: str | None = None) -> None:
        segments, self._buffer = split_tts_segments(self._buffer, flush_all=True)
        scheduled_any = False
        for segment in segments:
            if segment.strip():
                self._segment_queue.put_nowait(segment)
                scheduled_any = True
        if self._next_index == 0 and not scheduled_any and fallback_text:
            self._segment_queue.put_nowait(fallback_text)

    async def wait_until_done(self) -> int:
        await self._segment_queue.put(None)
        await self._worker_task
        return self._next_index

    def cancel(self) -> None:
        self._worker_task.cancel()


async def _run_text_turn(
    websocket: WebSocket,
    *,
    organization_id: str,
    session_id: str,
    text: str,
    tts_config: dict,
) -> None:
    """
    client text_message/audio -> Agent -> assistant_text_delta(+assistant_audio_chunk)
    -> assistant_message_done -> audio_end

    텍스트 delta와 오디오 청크를 같은 턴 안에서 같이 흘려보낸다(ver3.md 2.2,
    web_call 출력은 항상 text_and_voice).
    """
    tts = _SentenceTtsStreamer(tts_config, websocket)

    async for event, data in stream_agent_turn(
        organization_id=organization_id,
        session_id=session_id,
        user_message=text,
        channel=CHANNEL,
        on_delta=tts.feed,
    ):
        if event == "delta":
            await _send(websocket, "assistant_text_delta", text=data["delta"])
            continue

        if event == "turn_end":
            if not data.get("ai_enabled", True):
                tts.cancel()
                await _send(websocket, "ai_disabled", message=data["answer"])
                continue

            tts.flush(fallback_text=data["answer"])
            total_chunks = await tts.wait_until_done()
            await _send(websocket, "audio_end", total_chunks=total_chunks)

            await _send(
                websocket,
                "assistant_message_done",
                message=data["answer"],
                follow_up_message=data.get("follow_up_message"),
                conversation_id=data.get("conversation_id"),
            )

            if data.get("should_end_session"):
                await _send(
                    websocket,
                    "call_end",
                    conversation_id=data.get("conversation_id"),
                    reason="user_requested",
                )


@router.websocket("/ws")
async def web_call_ws(websocket: WebSocket, organization_id: str, session_id: str):
    """
    web_call 전용 WebSocket. ver3.md 7장 이벤트 설계를 따른다.

    4단계(현재) 범위: text_message 입력에 문장 단위 TTS streaming까지 붙인다.
    audio_chunk(STT)/interrupt는 5~6단계에서 추가한다.
    """
    await websocket.accept()
    ai_settings = await asyncio.to_thread(get_ai_settings, organization_id)

    if _resolve_voice_mode(ai_settings) != "pipeline":
        await _send(websocket, "error", message="Pipeline voice mode is disabled")
        await websocket.close()
        return

    tts_config = resolve_tts_config(ai_settings)

    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            if "text" not in message or message["text"] is None:
                continue

            try:
                payload = json.loads(message["text"])
            except json.JSONDecodeError:
                continue

            msg_type = payload.get("type")

            if msg_type == "text_message":
                text = str(payload.get("text") or "").strip()
                if not text:
                    continue
                await _run_text_turn(
                    websocket,
                    organization_id=organization_id,
                    session_id=session_id,
                    text=text,
                    tts_config=tts_config,
                )

            elif msg_type == "call_end":
                break

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("web_call websocket turn failed")
        try:
            await _send(websocket, "error", message="Web call turn failed")
        except Exception:
            pass


# --- realtime 모드 (속도 비교용) ---------------------------------------
#
# pipeline(/web-call/ws)과 달리 realtime은 음성을 OpenAI Realtime이 직접
# 처리한다(서버는 오디오를 보지 않는다). 클라이언트가 WebRTC로 OpenAI에 붙고,
# 사용자가 말할 때마다 OpenAI가 query_agent 함수를 호출하면 클라이언트가
# 그 결과를 받아오기 위해 /web-call/realtime/query를 호출해야 한다.
#
# 이 경로는 LangGraph 전체(지식검색/규칙/태스크)를 한 번의 run_agent_turn
# 호출로만 거치므로, decision/knowledge/task 노드의 멀티턴 제어는 pipeline
# 모드보다 얕다. 속도 비교 실험 목적의 보조 경로다.


def build_web_call_realtime_session_config(ai_settings: dict | None = None) -> dict:
    realtime_model = (
        ai_settings.get("realtime_model") if ai_settings else settings.openai_realtime_model
    )
    realtime_voice = (
        ai_settings.get("realtime_voice") if ai_settings else settings.openai_realtime_voice
    )

    return {
        "type": "realtime",
        "model": realtime_model,
        "instructions": (
            "너는 웹 음성 상담을 중계하는 음성 세션이다. "
            "사용자가 음성으로 말하든, 채팅으로 글을 입력하든 똑같이 처리한다. "
            "절대 자체 지식으로 답하지 않는다. 항상 아래 두 도구 중 하나를 호출해서 "
            "받은 결과만 그대로, 실제 상담원처럼 자연스럽게 말한다. "
            "도구 결과 내용을 추가하거나 바꾸지 않는다. "
            "사용자에게 AI, 함수, 도구, 시스템 같은 내부 구현 단어를 말하지 않는다.\n\n"
            "## search_knowledge — PROACTIVE\n"
            "Use when: 가격, 서비스 설명, 정책, 운영시간 등 정보를 묻는 질문.\n"
            "확인 없이 즉시 호출한다. 호출이 길어질 것 같으면 'Let me check that.' 같은 "
            "짧은 한 문장만 먼저 말하고 곧바로 호출한다.\n\n"
            "## task_action — CONFIRMATION FIRST\n"
            "Use when: 예약 생성·조회·취소·변경처럼 실제 상태를 바꾸는 요청.\n"
            "Confirmation phrase: 예약을 새로 시작하거나 취소/변경하기 전에는 "
            "'예약을 취소(또는 변경/등록)해드릴까요?'처럼 한 번 되물어 사용자가 "
            "동의한 다음에만 호출한다. 단, 이미 진행 중인 예약 대화를 이어가는 "
            "응답(예: 날짜·이름 등 정보 제공)에는 매번 재확인하지 않고 바로 호출한다."
        ),
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": realtime_voice},
        },
        "tools": [
            {
                "type": "function",
                "name": "search_knowledge",
                "description": (
                    "회사 지식 베이스(가격, 서비스 설명, 정책 등)를 검색해 답변을 받는다. "
                    "정보 조회뿐이라 부작용이 없으므로 확인 없이 바로 호출한다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "사용자가 방금 물어본 내용을 빠짐없이 정리한 한국어 문장",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "task_action",
                "description": (
                    "예약 생성·조회·취소·변경을 시작하거나 이어간다. 실제 상태를 "
                    "바꾸는 작업이므로 새로 시작/취소/변경할 때는 사용자 확인을 받은 "
                    "뒤에 호출한다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "사용자가 방금 말하거나 입력한 내용을 빠짐없이 정리한 한국어 문장",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
        ],
        # required는 매 turn마다 tool 호출을 강제해, 에코/잡음으로 생긴 허위
        # turn에도 모델이 message를 지어내 tool을 호출하게 만든다(사용자가
        # 말하지 않은 내용으로 AI가 혼자 대화를 이어가는 증상의 원인). instructions
        # 에 이미 "tool 호출 없이는 절대 답하지 마라"고 명시했으므로 auto로도
        # 실제 질문에는 정상적으로 호출된다.
        "tool_choice": "auto",
    }


@router.post("/realtime")
async def create_web_call_realtime_session(
    request: Request,
    organization_id: str = Query(...),
    session_id: str = Query(...),
):
    """
    클라이언트(브라우저)가 SDP offer를 보내면 OpenAI Realtime에 중계하고
    answer SDP를 그대로 돌려준다. 오디오는 이후 클라이언트 <-> OpenAI 사이에서
    직접 흐른다.
    """
    ai_settings = await asyncio.to_thread(get_ai_settings, organization_id)

    if not ai_settings.get("voice_enabled", True):
        raise HTTPException(status_code=409, detail="Voice is disabled")

    if _resolve_voice_mode(ai_settings) != "realtime":
        raise HTTPException(status_code=409, detail="Realtime voice mode is disabled")

    if request.headers.get("content-type", "").split(";", 1)[0] != "application/sdp":
        raise HTTPException(status_code=415, detail="Content-Type must be application/sdp")

    sdp = (await request.body()).decode("utf-8")
    if not sdp.strip():
        raise HTTPException(status_code=400, detail="SDP offer is required")

    safety_identifier = hashlib.sha256(
        f"web_call:{organization_id}:{session_id}".encode("utf-8")
    ).hexdigest()

    files = {
        "sdp": (None, sdp, "application/sdp"),
        "session": (
            None,
            json.dumps(build_web_call_realtime_session_config(ai_settings), ensure_ascii=False),
            "application/json",
        ),
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "OpenAI-Safety-Identifier": safety_identifier,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            openai_response = await client.post(OPENAI_REALTIME_CALLS_URL, headers=headers, files=files)
    except httpx.HTTPError:
        logger.exception("web_call Realtime session creation failed")
        raise HTTPException(status_code=502, detail=REALTIME_ERROR_MESSAGE)

    if not openai_response.is_success:
        logger.error(
            "web_call Realtime rejected session: status=%s body=%s",
            openai_response.status_code,
            openai_response.text[:500],
        )
        raise HTTPException(status_code=502, detail=REALTIME_ERROR_MESSAGE)

    return Response(content=openai_response.text, media_type="application/sdp")


class RealtimeQueryRequest(BaseModel):
    organization_id: str = Field(..., example="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da")
    session_id: str = Field(..., example="web_call_realtime_test")
    message: str = Field(..., example="안녕하세요")


# OpenAI Realtime 세션의 search_knowledge/task_action tool이 호출됐을 때
# 그 결과를 만들어주는 webhook들. 음성 발화든 data channel로 들어온 텍스트든
# OpenAI가 똑같이 이 tool들을 호출하므로 입력 경로와 무관하게 공통이다.
#
# 클라이언트(프론트)는 텍스트를 이 엔드포인트로 직접 보내면 안 되고, 반드시
# Realtime data channel(conversation.item.create)을 거쳐 OpenAI가 tool을
# 호출하게 해야 같은 대화 맥락(transcript, 끼어들기 등)이 유지된다.
#
# 받은 answer는 클라이언트가 function_call_output으로 Realtime에 돌려줘서
# 모델이 그걸 그대로 음성+텍스트로 말하게 한다.


def _save_realtime_turn_background(
    *,
    organization_id: str,
    session_id: str,
    user_message: str,
    answer: str,
) -> None:
    """
    realtime_search_knowledge는 run_agent_turn(LangGraph)을 거치지 않아
    conversation_node/save_ai_message 같은 메시지 저장 경로를 안 타므로,
    관리자가 통화 내용을 다시 볼 수 있도록 직접 저장한다. 응답 경로(answer
    반환)를 막지 않도록 백그라운드 스레드에서 처리한다.
    """

    def _save():
        try:
            conversation = get_or_create_conversation(
                organization_id=organization_id,
                session_id=session_id,
                channel=CHANNEL,
            )
            conversation_id = conversation["id"]

            create_conversation_message(
                organization_id=organization_id,
                conversation_id=conversation_id,
                sender_type="customer",
                sender_name="Customer",
                message=user_message,
                metadata={"session_id": session_id, "channel": CHANNEL},
            )
            create_conversation_message(
                organization_id=organization_id,
                conversation_id=conversation_id,
                sender_type="ai",
                sender_name="Front Agent",
                message=answer,
                metadata={"session_id": session_id, "channel": CHANNEL, "source": "realtime_search_knowledge"},
            )
            update_conversation_last_message(
                organization_id=organization_id,
                conversation_id=conversation_id,
                last_message=answer,
            )
        except Exception:
            logger.warning(
                "Failed to save realtime search_knowledge turn: organization_id=%s session_id=%s",
                organization_id,
                session_id,
                exc_info=True,
            )

    threading.Thread(target=_save, daemon=True).start()


@router.post("/realtime/search-knowledge")
async def realtime_search_knowledge(req: RealtimeQueryRequest):
    """
    search_knowledge tool 결과. 순수 조회라 LangGraph 전체를 거치지 않고
    retrieve_knowledge만 직접 호출해 지식검색 한 단계만 처리한다.

    LangGraph를 안 거치므로 conversation_node의 메시지 저장 로직도 안 타는데,
    관리자가 통화 내용 전체(지식검색 포함)를 다시 볼 수 있어야 하므로
    여기서 직접 저장한다.
    """
    chunks = await retrieve_knowledge(organization_id=req.organization_id, query=req.message)

    if not chunks:
        answer = "확인해보니 관련 정보를 찾지 못했습니다. 담당자에게 다시 확인 후 안내드리겠습니다."
    else:
        answer = "\n".join(chunk.get("content", "") for chunk in chunks)

    _save_realtime_turn_background(
        organization_id=req.organization_id,
        session_id=req.session_id,
        user_message=req.message,
        answer=answer,
    )

    return {"answer": answer}


@router.post("/realtime/task-action")
async def realtime_task_action(req: RealtimeQueryRequest):
    """
    task_action tool 결과. task_type 분류(예약 생성/조회/취소/변경 구분)는
    decision_node가 이미 갖고 있는 로직이라 별도로 다시 만들지 않고
    run_agent_turn(LangGraph 전체)에 맡긴다. 진행 중인 예약 세션이 있으면
    task_router_node가 decision_node를 건너뛰고 바로 이어간다.
    """
    result = await run_agent_turn(
        organization_id=req.organization_id,
        session_id=req.session_id,
        user_message=req.message,
        channel=CHANNEL,
    )

    answer = result.get("final_response") or AI_DISABLED_MESSAGE
    return {
        "answer": answer,
        "conversation_id": result.get("conversation_id"),
        "should_end_session": bool(result.get("should_end_session")),
    }
