import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import time
import wave

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.chat import (
    AI_DISABLED_MESSAGE,
    AGENT_ERROR_MESSAGE,
    NODE_TRACE_LABELS,
    build_trace_detail,
    elapsed_ms_since,
    sse_event,
)
from app.core.config import settings
from app.graph.graph_runtime import build_initial_state, get_agent_graph, graph_config_for
from app.repositories.ai_usage_repo import create_usage_log_background
from app.repositories.conversation_repo import end_call_conversation
from app.repositories.organization_ai_settings_repo import get_ai_settings


router = APIRouter(prefix="/voice", tags=["Voice"])
logger = logging.getLogger(__name__)
OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
REALTIME_ERROR_MESSAGE = "Realtime voice connection failed"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
# ElevenLabs는 raw PCM을 주므로 이 샘플레이트로 받아 WAV 헤더를 씌워 통일한다.
ELEVENLABS_PCM_RATE = 24000
# wav(PCM)는 청크 경계에 인코더 패딩/갭이 없어 문장 단위 순차 재생이 안정적이다.
TTS_RESPONSE_FORMAT = "wav"
TTS_CONTENT_TYPE = "audio/wav"
# 문장 청크 TTS를 동시에 몇 개까지 합성할지. 첫 오디오는 빨리, OpenAI 동시 호출은 제한.
TTS_MAX_CONCURRENCY = 3
SUPPORTED_TRANSCRIPTION_UPLOADS = {
    "audio/flac": ("utterance.flac", "audio/flac"),
    "audio/x-flac": ("utterance.flac", "audio/flac"),
    "audio/m4a": ("utterance.m4a", "audio/m4a"),
    "audio/mp4": ("utterance.mp4", "audio/mp4"),
    "audio/mpeg": ("utterance.mp3", "audio/mpeg"),
    "audio/mp3": ("utterance.mp3", "audio/mpeg"),
    "audio/mpga": ("utterance.mpga", "audio/mpga"),
    "audio/oga": ("utterance.oga", "audio/oga"),
    "audio/ogg": ("utterance.ogg", "audio/ogg"),
    "audio/wav": ("utterance.wav", "audio/wav"),
    "audio/x-wav": ("utterance.wav", "audio/wav"),
    "audio/webm": ("utterance.webm", "audio/webm"),
}
KOREAN_DIGITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
KOREAN_SMALL_UNITS = ["", "십", "백", "천"]
KOREAN_BIG_UNITS = ["", "만", "억", "조"]
KOREAN_HOURS = {
    1: "한",
    2: "두",
    3: "세",
    4: "네",
    5: "다섯",
    6: "여섯",
    7: "일곱",
    8: "여덟",
    9: "아홉",
    10: "열",
    11: "열한",
    12: "열두",
}
KOREAN_PHONE_DIGITS = {
    "0": "공",
    "1": "일",
    "2": "이",
    "3": "삼",
    "4": "사",
    "5": "오",
    "6": "육",
    "7": "칠",
    "8": "팔",
    "9": "구",
}
KOREAN_MONTHS = {
    1: "일월",
    2: "이월",
    3: "삼월",
    4: "사월",
    5: "오월",
    6: "유월",
    7: "칠월",
    8: "팔월",
    9: "구월",
    10: "시월",
    11: "십일월",
    12: "십이월",
}


class SpeechRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    organization_id: str | None = None
    session_id: str | None = None
    channel: str = "web_call"


class VoiceTurnResponse(BaseModel):
    conversation_id: str | None = None
    transcript: str
    answer: str
    messages: list[dict] = Field(default_factory=list)
    audio_base64: str
    audio_content_type: str = TTS_CONTENT_TYPE


def normalize_transcription_upload(content_type: str | None) -> tuple[str, str]:
    """
    Browser MediaRecorder는 audio/webm;codecs=opus처럼 codec 파라미터가 붙은
    content-type을 보낼 수 있다. OpenAI transcription multipart에는 지원되는
    확장자와 단순 mime을 맞춰 보내는 편이 안정적이다.
    """

    normalized = (content_type or "audio/webm").split(";", 1)[0].strip().lower()
    return SUPPORTED_TRANSCRIPTION_UPLOADS.get(
        normalized,
        ("utterance.webm", "audio/webm"),
    )


def _read_korean_under_10000(value: int) -> str:
    if value == 0:
        return ""

    parts: list[str] = []
    digits = list(map(int, str(value).zfill(4)))
    for index, digit in enumerate(digits):
        if digit == 0:
            continue
        unit_index = 3 - index
        digit_word = "" if digit == 1 and unit_index > 0 else KOREAN_DIGITS[digit]
        parts.append(f"{digit_word}{KOREAN_SMALL_UNITS[unit_index]}")
    return "".join(parts)


def read_korean_number(value: int) -> str:
    if value == 0:
        return "영"
    if value < 0:
        return f"마이너스 {read_korean_number(abs(value))}"

    groups: list[str] = []
    group_index = 0
    remaining = value
    while remaining > 0 and group_index < len(KOREAN_BIG_UNITS):
        chunk = remaining % 10000
        if chunk:
            chunk_text = _read_korean_under_10000(chunk)
            groups.append(f"{chunk_text}{KOREAN_BIG_UNITS[group_index]}")
        remaining //= 10000
        group_index += 1

    if remaining > 0:
        groups.append(str(remaining))

    return "".join(reversed(groups))


def read_korean_time(hour: int, minute: int) -> str:
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return f"{hour}:{minute:02d}"

    period = "오전" if hour < 12 else "오후"
    display_hour = hour % 12 or 12
    hour_text = KOREAN_HOURS.get(display_hour, read_korean_number(display_hour))
    if minute == 0:
        return f"{period} {hour_text} 시"
    return f"{period} {hour_text} 시 {read_korean_number(minute)} 분"


def read_korean_date(year: int, month: int, day: int) -> str:
    if year < 1 or month < 1 or month > 12 or day < 1 or day > 31:
        return f"{year}-{month:02d}-{day:02d}"

    return f"{KOREAN_MONTHS[month]} {read_korean_number(day)} 일"


def read_phone_number(value: str) -> str:
    groups = re.split(r"[-\s]+", value)
    spoken_groups = [
        "".join(KOREAN_PHONE_DIGITS[digit] for digit in group if digit.isdigit())
        for group in groups
    ]
    return " ".join(group for group in spoken_groups if group)


def normalize_text_for_korean_speech(text: str) -> str:
    """
    TTS가 15:00을 "십오 공공"처럼 읽는 문제를 줄이기 위한 음성용 전처리.
    화면에 보여줄 답변 원문은 바꾸지 않고 TTS 입력에만 사용한다.
    """

    normalized = text

    normalized = re.sub(
        r"(?<!\d)(0\d{1,2})[-\s](\d{3,4})[-\s](\d{4})(?!\d)",
        lambda match: read_phone_number(match.group(0)),
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?!\d)",
        lambda match: read_korean_date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        lambda match: read_korean_date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)",
        lambda match: read_korean_time(int(match.group(1)), int(match.group(2))),
        normalized,
    )
    normalized = re.sub(
        r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)\s*원",
        lambda match: f"{read_korean_number(int(match.group(1).replace(',', '')))} 원",
        normalized,
    )
    normalized = re.sub(
        r"(?<![\w.])(\d{1,3}(?:,\d{3})+)(?![\w.])",
        lambda match: read_korean_number(int(match.group(1).replace(",", ""))),
        normalized,
    )

    return normalized


# 스트리밍 TTS용 문장 분할 파라미터.
# delta가 쌓이다 문장 경계를 만나면 그 구간만 먼저 합성해 오디오를 흘려보낸다.
# 숫자 사이의 마침표(1.5)는 경계로 보지 않아 금액/날짜 전처리가 깨지지 않게 한다.
TTS_BOUNDARY_RE = re.compile(r"(?<!\d)[.!?。！？](?!\d)|\n")
MIN_TTS_SEGMENT_CHARS = 12
MAX_TTS_SEGMENT_CHARS = 160


def split_tts_segments(buffer: str, *, flush_all: bool = False) -> tuple[list[str], str]:
    """
    누적된 텍스트 버퍼에서 합성 가능한 문장 구간을 잘라낸다.

    문장 부호를 만나면 그 앞까지를 한 구간으로 떼어내고, 부호 없이 너무 길어지면
    강제로 끊어 첫 오디오가 늦지 않게 한다. flush_all이면 남은 버퍼도 모두 내보낸다.
    """

    segments: list[str] = []
    while True:
        match = next(
            (m for m in TTS_BOUNDARY_RE.finditer(buffer) if m.end() >= MIN_TTS_SEGMENT_CHARS),
            None,
        )
        if match:
            segments.append(buffer[: match.end()])
            buffer = buffer[match.end() :]
            continue
        if len(buffer) >= MAX_TTS_SEGMENT_CHARS:
            segments.append(buffer[:MAX_TTS_SEGMENT_CHARS])
            buffer = buffer[MAX_TTS_SEGMENT_CHARS:]
            continue
        break

    if flush_all and buffer.strip():
        segments.append(buffer)
        buffer = ""

    return segments, buffer


async def read_audio_upload(audio: UploadFile) -> bytes:
    content = await audio.read(settings.voice_upload_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Audio file is required")
    if len(content) > settings.voice_upload_max_bytes:
        raise HTTPException(status_code=413, detail="Audio file is too large")
    return content


async def transcribe_audio_content(
    *,
    content: bytes,
    filename: str | None,
    content_type: str | None,
    model: str,
) -> tuple[str, dict]:
    upload_filename, upload_content_type = normalize_transcription_upload(content_type)
    logger.info(
        "transcription upload received: filename=%s content_type=%s normalized_filename=%s normalized_content_type=%s bytes=%s model=%s",
        filename,
        content_type,
        upload_filename,
        upload_content_type,
        len(content),
        model,
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            openai_response = await client.post(
                OPENAI_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={
                    "model": model,
                    "language": "ko",
                    "response_format": "json",
                },
                files={
                    "file": (
                        upload_filename,
                        content,
                        upload_content_type,
                    )
                },
            )
    except httpx.HTTPError:
        logger.exception("OpenAI transcription request failed")
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    if not openai_response.is_success:
        logger.error(
            "OpenAI transcription rejected request: status=%s filename=%s content_type=%s bytes=%s body=%s",
            openai_response.status_code,
            upload_filename,
            upload_content_type,
            len(content),
            openai_response.text[:1000],
        )
        if openai_response.status_code == 400:
            raise HTTPException(
                status_code=422,
                detail="Uploaded audio could not be processed",
            )
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    text = str(openai_response.json().get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="No speech was detected")

    metadata = {
        "filename": filename,
        "content_type": content_type,
        "normalized_filename": upload_filename,
        "normalized_content_type": upload_content_type,
    }
    return text, metadata


def resolve_tts_config(ai_settings: dict | None) -> dict:
    """
    조직 설정(있으면)과 env 기본값을 합쳐 TTS provider 설정을 만든다.
    provider를 openai/elevenlabs로 바꾸기만 하면 합성 경로가 전환된다.
    """

    source = ai_settings or {}
    provider = (source.get("voice_tts_provider") or settings.tts_provider or "openai").strip().lower()
    return {
        "provider": provider if provider in {"openai", "elevenlabs"} else "openai",
        "model": source.get("voice_tts_model") or settings.voice_tts_model,
        "voice": source.get("voice_tts_voice") or settings.voice_tts_voice,
        "elevenlabs_model": source.get("elevenlabs_model") or settings.elevenlabs_model,
        "elevenlabs_voice_id": source.get("elevenlabs_voice_id") or settings.elevenlabs_voice_id,
    }


def tts_log_fields(tts: dict) -> tuple[str, str]:
    """usage 로그에 남길 (provider, model)."""
    if tts.get("provider") == "elevenlabs":
        return "elevenlabs", (tts.get("elevenlabs_model") or settings.elevenlabs_model)
    return "openai", (tts.get("model") or settings.voice_tts_model)


def _pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """raw 16-bit PCM에 WAV 헤더를 씌워 프론트의 wav 청크 재생 경로와 통일한다."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


async def _synthesize_openai(speech_text: str, model: str, voice: str, response_format: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            openai_response = await client.post(
                OPENAI_SPEECH_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "voice": voice,
                    "input": speech_text,
                    "response_format": response_format,
                },
            )
    except httpx.HTTPError:
        logger.exception("OpenAI speech request failed")
        raise HTTPException(status_code=502, detail="Speech generation failed")

    if not openai_response.is_success:
        logger.error(
            "OpenAI speech rejected request: status=%s body=%s",
            openai_response.status_code,
            openai_response.text[:500],
        )
        raise HTTPException(status_code=502, detail="Speech generation failed")

    return openai_response.content


async def _synthesize_elevenlabs(speech_text: str, tts: dict) -> bytes:
    api_key = settings.elevenlabs_api_key
    voice_id = tts.get("elevenlabs_voice_id")
    model_id = tts.get("elevenlabs_model") or settings.elevenlabs_model

    if not api_key:
        logger.error("ElevenLabs API key is not configured")
        raise HTTPException(status_code=500, detail="ElevenLabs is not configured")
    if not voice_id:
        logger.error("ElevenLabs voice_id is not configured")
        raise HTTPException(status_code=500, detail="ElevenLabs voice is not configured")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{ELEVENLABS_TTS_URL}/{voice_id}",
                params={"output_format": f"pcm_{ELEVENLABS_PCM_RATE}"},
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={"text": speech_text, "model_id": model_id},
            )
    except httpx.HTTPError:
        logger.exception("ElevenLabs speech request failed")
        raise HTTPException(status_code=502, detail="Speech generation failed")

    if not response.is_success:
        logger.error(
            "ElevenLabs speech rejected request: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        raise HTTPException(status_code=502, detail="Speech generation failed")

    return _pcm16_to_wav(response.content, ELEVENLABS_PCM_RATE)


async def synthesize_speech_content(
    *,
    text: str,
    tts: dict,
    response_format: str = TTS_RESPONSE_FORMAT,
) -> bytes:
    """
    TTS provider(openai/elevenlabs)에 따라 합성한다. 어느 쪽이든 wav 바이트를 반환해
    스트리밍 청크 재생 경로(content_type=audio/wav)와 일관되게 한다.
    """
    speech_text = normalize_text_for_korean_speech(text)
    if tts.get("provider") == "elevenlabs":
        return await _synthesize_elevenlabs(speech_text, tts)
    return await _synthesize_openai(
        speech_text,
        tts.get("model") or settings.voice_tts_model,
        tts.get("voice") or settings.voice_tts_voice,
        response_format,
    )


def build_voice_agent_message(transcript: str, interrupt_context: str | None = None) -> str:
    transcript = transcript.strip()
    if not interrupt_context:
        return transcript

    return (
        "[통화 끼어들기]\n"
        "사용자가 이전 응답 또는 처리 중간에 다시 말했습니다. "
        "이전 맥락을 유지하되 새 발화를 우선 반영해 자연스럽게 이어서 답변하세요.\n\n"
        f"끼어든 발화: {transcript}\n"
        f"프론트 컨텍스트: {interrupt_context}"
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
                "provider": "openai",
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
        response_started = False

        # 스트리밍 TTS 상태. 문장이 완성되면 백그라운드 task로 합성하고,
        # 완성된 오디오 청크는 delta 사이사이 흘려보낸다(delta 방출을 막지 않음).
        tts_buffer = ""
        audio_index = 0
        total_audio_bytes = 0
        audio_queue: asyncio.Queue[str] = asyncio.Queue()
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
                sse_event(
                    "audio",
                    {
                        "content_type": TTS_CONTENT_TYPE,
                        "audio_base64": base64.b64encode(chunk_audio).decode("ascii"),
                        "index": index,
                        "elapsed_ms": elapsed_ms_since(started_at),
                    },
                )
            )

        def schedule_segment(segment_text: str) -> None:
            nonlocal audio_index
            if not segment_text.strip():
                return
            index = audio_index
            audio_index += 1
            tts_tasks.append(asyncio.create_task(synth_worker(index, segment_text)))

        def drain_ready_audio() -> list[str]:
            ready: list[str] = []
            while True:
                try:
                    ready.append(audio_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return ready

        async for mode, chunk in agent_graph.astream(
            initial_state,
            config=config,
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                if chunk.get("type") == "ai_response_delta":
                    if not response_started:
                        yield sse_event(
                            "response_start",
                            {"elapsed_ms": elapsed_ms_since(started_at)},
                        )
                        response_started = True

                    delta = str(chunk.get("delta") or "")
                    answer_chunks.append(delta)
                    yield sse_event(
                        "delta",
                        {
                            "delta": delta,
                            "elapsed_ms": elapsed_ms_since(started_at),
                        },
                    )

                    # 문장이 완성된 만큼만 백그라운드 합성 예약하고, 준비된 오디오를 흘려보낸다.
                    tts_buffer += delta
                    segments, tts_buffer = split_tts_segments(tts_buffer)
                    for segment in segments:
                        schedule_segment(segment)
                    for audio_event in drain_ready_audio():
                        yield audio_event
                elif chunk.get("type") == "knowledge_start":
                    yield sse_event(
                        "knowledge_start",
                        {
                            "queries": chunk.get("queries", []),
                            "elapsed_ms": elapsed_ms_since(started_at),
                        },
                    )
                elif chunk.get("type") == "task_step":
                    step = chunk.get("step") or {}
                    label = step.get("node_label") or step.get("node_key") or "태스크 단계"
                    yield sse_event(
                        "trace",
                        {
                            "step": "task",
                            "status": "step",
                            "detail": (
                                f"{label} / "
                                f"type={step.get('node_type')} / "
                                f"next={step.get('next_behavior')}"
                            ),
                            "items": [step],
                            "elapsed_ms": elapsed_ms_since(started_at),
                        },
                    )
                continue

            for node_name, node_state in chunk.items():
                final_state.update(node_state)

                if node_name == "conversation":
                    yield sse_event(
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

                label = NODE_TRACE_LABELS.get(node_name)
                if not label:
                    continue

                detail, items = build_trace_detail(node_name, node_state)
                yield sse_event(
                    "trace",
                    {
                        "step": node_name,
                        "status": "done",
                        "detail": detail or label,
                        "items": items,
                        "elapsed_ms": elapsed_ms_since(started_at),
                    },
                )

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

        # 남은 버퍼(마지막 문장)를 합성 예약한다.
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


def get_voice_mode(organization_id: str | None = None) -> str:
    if organization_id:
        mode = str(get_ai_settings(organization_id).get("voice_mode") or "").strip().lower()
    else:
        mode = settings.voice_mode.strip().lower()

    return mode if mode in {"pipeline", "realtime"} else "pipeline"


@router.get("/config")
async def voice_config(organization_id: str | None = None):
    if organization_id:
        ai_settings = get_ai_settings(organization_id)
        return {
            "organization_id": organization_id,
            "enabled": ai_settings.get("voice_enabled", True),
            "mode": get_voice_mode(organization_id),
            "stt_model": ai_settings.get("voice_stt_model"),
            "tts_provider": resolve_tts_config(ai_settings)["provider"],
            "tts_model": ai_settings.get("voice_tts_model"),
            "tts_voice": ai_settings.get("voice_tts_voice"),
            "realtime_model": ai_settings.get("realtime_model"),
            "realtime_voice": ai_settings.get("realtime_voice"),
            "response_style": ai_settings.get("voice_response_style"),
        }

    return {
        "mode": get_voice_mode(),
        "stt_model": settings.voice_stt_model,
        "tts_model": settings.voice_tts_model,
        "tts_voice": settings.voice_tts_voice,
    }


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    organization_id: str | None = Query(None),
    session_id: str | None = Query(None),
):
    ai_settings = get_ai_settings(organization_id) if organization_id else None
    stt_model = (
        ai_settings.get("voice_stt_model")
        if ai_settings
        else settings.voice_stt_model
    )

    content = await read_audio_upload(audio)
    text, upload_metadata = await transcribe_audio_content(
        content=content,
        filename=audio.filename,
        content_type=audio.content_type,
        model=stt_model,
    )

    if organization_id:
        create_usage_log_background(
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "channel": "web_call",
                "feature": "stt",
                "provider": "openai",
                "model": stt_model,
                "audio_bytes": len(content),
                "text_chars": len(text),
                "metadata": upload_metadata,
            }
        )

    return {"text": text}


@router.post("/speech")
async def synthesize_speech(payload: SpeechRequest):
    ai_settings = (
        get_ai_settings(payload.organization_id)
        if payload.organization_id
        else None
    )
    tts_config = resolve_tts_config(ai_settings)
    tts_provider, tts_log_model = tts_log_fields(tts_config)

    speech_content = await synthesize_speech_content(
        text=payload.text,
        tts=tts_config,
    )

    if payload.organization_id:
        create_usage_log_background(
            {
                "organization_id": payload.organization_id,
                "session_id": payload.session_id,
                "channel": payload.channel,
                "feature": "tts",
                "provider": tts_provider,
                "model": tts_log_model,
                "audio_bytes": len(speech_content),
                "text_chars": len(payload.text),
                "metadata": {
                    "voice": tts_config.get("voice"),
                    "tts_provider": tts_provider,
                    "response_format": TTS_RESPONSE_FORMAT,
                },
            }
        )

    return Response(content=speech_content, media_type=TTS_CONTENT_TYPE)


@router.post("/turn", response_model=VoiceTurnResponse)
async def process_voice_turn(
    audio: UploadFile = File(...),
    organization_id: str = Form(...),
    session_id: str = Form(...),
    interrupt_context: str | None = Form(None),
):
    ai_settings = get_ai_settings(organization_id)
    if not ai_settings.get("voice_enabled", True):
        raise HTTPException(status_code=409, detail="Voice is disabled")

    content = await read_audio_upload(audio)
    stt_model = ai_settings.get("voice_stt_model") or settings.voice_stt_model
    tts_config = resolve_tts_config(ai_settings)
    tts_provider, tts_log_model = tts_log_fields(tts_config)

    transcript, upload_metadata = await transcribe_audio_content(
        content=content,
        filename=audio.filename,
        content_type=audio.content_type,
        model=stt_model,
    )
    create_usage_log_background(
        {
            "organization_id": organization_id,
            "session_id": session_id,
            "channel": "web_call",
            "feature": "stt",
            "provider": "openai",
            "model": stt_model,
            "audio_bytes": len(content),
            "text_chars": len(transcript),
            "metadata": upload_metadata,
        }
    )

    result = await run_voice_agent_turn(
        organization_id=organization_id,
        session_id=session_id,
        transcript=transcript,
        interrupt_context=interrupt_context,
    )
    answer = str(result.get("final_response") or "").strip()
    if not answer:
        raise HTTPException(status_code=502, detail="Agent response is empty")

    speech_content = await synthesize_speech_content(
        text=answer,
        tts=tts_config,
    )
    create_usage_log_background(
        {
            "organization_id": organization_id,
            "session_id": session_id,
            "channel": "web_call",
            "feature": "tts",
            "provider": tts_provider,
            "model": tts_log_model,
            "audio_bytes": len(speech_content),
            "text_chars": len(answer),
            "metadata": {
                "voice": tts_config.get("voice"),
                "tts_provider": tts_provider,
                "response_format": TTS_RESPONSE_FORMAT,
                "voice_turn": True,
            },
        }
    )

    return VoiceTurnResponse(
        conversation_id=result.get("conversation_id"),
        transcript=transcript,
        answer=answer,
        messages=[
            {
                "conversation_id": result.get("conversation_id"),
                "sender_type": "customer",
                "sender_name": "Customer",
                "message": transcript,
                "metadata": {
                    "session_id": session_id,
                    "channel": "web_call",
                    "source": "voice_transcript",
                },
            },
            {
                "conversation_id": result.get("conversation_id"),
                "sender_type": "ai",
                "sender_name": "Front Agent",
                "message": answer,
                "metadata": {
                    "session_id": session_id,
                    "channel": "web_call",
                    "source": "voice_answer",
                },
            },
        ],
        audio_base64=base64.b64encode(speech_content).decode("ascii"),
    )


@router.post("/pipeline/turn/stream")
async def stream_pipeline_voice_turn(
    audio: UploadFile = File(...),
    organization_id: str = Form(...),
    session_id: str = Form(...),
    interrupt_context: str | None = Form(None),
):
    ai_settings = get_ai_settings(organization_id)
    if not ai_settings.get("voice_enabled", True):
        raise HTTPException(status_code=409, detail="Voice is disabled")

    if get_voice_mode(organization_id) != "pipeline":
        raise HTTPException(status_code=409, detail="Pipeline voice mode is disabled")

    content = await read_audio_upload(audio)
    return StreamingResponse(
        stream_pipeline_voice_turn_events(
            content=content,
            filename=audio.filename,
            content_type=audio.content_type,
            organization_id=organization_id,
            session_id=session_id,
            interrupt_context=interrupt_context,
            ai_settings=ai_settings,
        ),
        media_type="text/event-stream",
    )


def build_realtime_session_config(ai_settings: dict | None = None) -> dict:
    realtime_model = (
        ai_settings.get("realtime_model")
        if ai_settings
        else settings.openai_realtime_model
    )
    realtime_voice = (
        ai_settings.get("realtime_voice")
        if ai_settings
        else settings.openai_realtime_voice
    )

    return {
        "type": "realtime",
        "model": realtime_model,
        "instructions": (
            "너는 전화 상담을 중계하는 음성 세션이다. "
            "사용자가 말할 때마다 query_agent 함수를 정확히 한 번 호출하고, "
            "message에는 사용자의 발화를 한국어 텍스트로 전달한다. "
            "함수 결과를 받기 전에는 자체 지식으로 답하지 않는다. "
            "함수 결과를 받은 뒤에는 내용을 추가하거나 바꾸지 말고 실제 상담원처럼 자연스럽게 읽는다. "
            "사용자에게 AI, 함수, 시스템 같은 내부 구현 단어를 말하지 않는다."
        ),
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "voice": realtime_voice,
            },
        },
        "tools": [
            {
                "type": "function",
                "name": "query_agent",
                "description": "사용자 발화를 Front Agent LangGraph에 전달해 최종 답변을 받는다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "사용자가 방금 말한 내용을 빠짐없이 정리한 한국어 문장",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
        ],
        "tool_choice": "required",
    }


@router.post("/realtime")
async def create_realtime_call(
    request: Request,
    organization_id: str = Query(...),
    session_id: str = Query(...),
):
    ai_settings = get_ai_settings(organization_id)

    if not ai_settings.get("voice_enabled", True):
        raise HTTPException(status_code=409, detail="Voice is disabled")

    if get_voice_mode(organization_id) != "realtime":
        raise HTTPException(status_code=409, detail="Realtime voice mode is disabled")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/sdp":
        raise HTTPException(status_code=415, detail="Content-Type must be application/sdp")

    sdp = (await request.body()).decode("utf-8")

    if not sdp.strip():
        raise HTTPException(status_code=400, detail="SDP offer is required")

    safety_identifier = hashlib.sha256(
        f"{organization_id}:{session_id}".encode("utf-8")
    ).hexdigest()

    files = {
        "sdp": (None, sdp, "application/sdp"),
        "session": (
            None,
            json.dumps(build_realtime_session_config(ai_settings), ensure_ascii=False),
            "application/json",
        ),
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "OpenAI-Safety-Identifier": safety_identifier,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            openai_response = await client.post(
                OPENAI_REALTIME_CALLS_URL,
                headers=headers,
                files=files,
            )
    except httpx.HTTPError:
        logger.exception("OpenAI Realtime call creation failed")
        raise HTTPException(status_code=502, detail=REALTIME_ERROR_MESSAGE)

    if not openai_response.is_success:
        logger.error(
            "OpenAI Realtime rejected call: status=%s body=%s",
            openai_response.status_code,
            openai_response.text[:500],
        )
        raise HTTPException(status_code=502, detail=REALTIME_ERROR_MESSAGE)

    return Response(
        content=openai_response.text,
        media_type="application/sdp",
    )


class CallEndRequest(BaseModel):
    organization_id: str
    session_id: str


@router.post("/call/end")
async def end_voice_call(payload: CallEndRequest):
    """
    클라이언트가 통화를 끊을 때 호출한다. call_started_at이 있는 통화 채널
    상담방에 call_ended_at/call_duration_seconds를 기록한다.

    클라이언트가 비정상 종료(새로고침, 강제 종료 등)되어 이 엔드포인트가
    호출되지 못하면 duration이 기록되지 않을 수 있다 — 통화 목록 화면에서는
    call_ended_at이 없는 항목을 "진행 중"으로 표시해 구분한다.
    """
    conversation = end_call_conversation(
        organization_id=payload.organization_id,
        session_id=payload.session_id,
    )

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": conversation.get("id"),
        "call_started_at": conversation.get("call_started_at"),
        "call_ended_at": conversation.get("call_ended_at"),
        "call_duration_seconds": conversation.get("call_duration_seconds"),
    }
