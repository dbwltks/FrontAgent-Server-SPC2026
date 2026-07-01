import base64
import hashlib
import json
import logging

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.repositories.ai_usage_repo import create_usage_log_background
from app.repositories.conversation_repo import end_call_conversation
from app.repositories.organization_ai_settings_repo import get_ai_settings
from app.services.voice_korean_text import (  # noqa: F401 (재노출, 기존 테스트 호환용)
    normalize_text_for_korean_speech,
    split_tts_segments,
)
from app.services.voice_pipeline import (
    run_voice_agent_turn,
    stream_pipeline_voice_turn_events,
)
from app.services.voice_stt import (
    read_audio_upload,
    SUPPORTED_TRANSCRIPTION_UPLOADS,
    transcribe_audio_content,
)
from app.services.voice_tts import (
    ELEVENLABS_PCM_RATE,  # noqa: F401 (재노출, 기존 테스트 호환용)
    TTS_CONTENT_TYPE,
    TTS_RESPONSE_FORMAT,
    _pcm16_to_wav,  # noqa: F401 (재노출, 기존 테스트 호환용)
    resolve_tts_config,
    synthesize_speech_content,
    tts_log_fields,
)

router = APIRouter(prefix="/voice", tags=["Voice"])
logger = logging.getLogger(__name__)
OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
REALTIME_ERROR_MESSAGE = "Realtime voice connection failed"
VOICE_UPLOAD_CONFIG = {
    # MediaRecorder 기본 webm/opus를 wav로 변환하지 말고 그대로 보내는 쪽이
    # 업로드 준비 시간과 전송 크기를 줄인다. voice_stt가 codec 파라미터를 제거해
    # OpenAI transcription multipart에 맞는 audio/webm으로 정규화한다.
    "preferred_upload_content_type": "audio/webm;codecs=opus",
    "preferred_upload_extension": "webm",
    "convert_to_wav_before_upload": False,
    "supported_upload_content_types": sorted(SUPPORTED_TRANSCRIPTION_UPLOADS.keys()),
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
    end_session: bool = False
    end_call: bool = False




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
        tts_config = resolve_tts_config(ai_settings)
        return {
            "organization_id": organization_id,
            "enabled": ai_settings.get("voice_enabled", True),
            "mode": get_voice_mode(organization_id),
            "stt_provider": ai_settings.get("voice_stt_provider") or settings.stt_provider,
            "stt_model": ai_settings.get("voice_stt_model") or settings.voice_stt_model,
            "tts_provider": tts_config["provider"],
            "tts_model": tts_config.get("model"),
            "tts_voice": tts_config.get("voice"),
            "elevenlabs_model": tts_config.get("elevenlabs_model"),
            "elevenlabs_voice_id": tts_config.get("elevenlabs_voice_id"),
            "realtime_model": ai_settings.get("realtime_model"),
            "realtime_voice": ai_settings.get("realtime_voice"),
            "response_style": ai_settings.get("voice_response_style"),
            "upload": VOICE_UPLOAD_CONFIG,
        }

    tts_config = resolve_tts_config(None)
    return {
        "mode": get_voice_mode(),
        "stt_provider": settings.stt_provider,
        "stt_model": settings.voice_stt_model,
        "tts_provider": tts_config["provider"],
        "tts_model": tts_config.get("model"),
        "tts_voice": tts_config.get("voice"),
        "elevenlabs_model": tts_config.get("elevenlabs_model"),
        "elevenlabs_voice_id": tts_config.get("elevenlabs_voice_id"),
        "realtime_model": settings.openai_realtime_model,
        "realtime_voice": settings.openai_realtime_voice,
        "upload": VOICE_UPLOAD_CONFIG,
    }


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    organization_id: str | None = Query(None),
    session_id: str | None = Query(None),
):
    ai_settings = get_ai_settings(organization_id) if organization_id else None
    stt_provider = (
        ai_settings.get("voice_stt_provider") if ai_settings else settings.stt_provider
    ) or "openai"
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
        provider=stt_provider,
    )

    if organization_id:
        create_usage_log_background(
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "channel": "web_call",
                "feature": "stt",
                "provider": stt_provider,
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
    stt_provider = ai_settings.get("voice_stt_provider") or settings.stt_provider
    stt_model = ai_settings.get("voice_stt_model") or settings.voice_stt_model
    tts_config = resolve_tts_config(ai_settings)
    tts_provider, tts_log_model = tts_log_fields(tts_config)

    transcript, upload_metadata = await transcribe_audio_content(
        content=content,
        filename=audio.filename,
        content_type=audio.content_type,
        model=stt_model,
        provider=stt_provider,
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
        end_session=bool(result.get("should_end_session")),
        end_call=bool(result.get("should_end_session")),
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
            "사용자가 실제로 의미 있는 말을 했을 때만 query_agent 함수를 정확히 한 번 "
            "호출하고, message에는 사용자의 발화를 한국어 텍스트로 전달한다. "
            "잡음, 숨소리, 무음, 알아들을 수 없는 짧은 소리처럼 실제 발화가 아닌 입력에는 "
            "절대 함수를 호출하지 않고 아무 말도 하지 않는다. "
            "함수 결과를 받기 전에는 자체 지식으로 답하지 않는다. "
            "함수 결과를 받은 뒤에는 내용을 추가하거나 바꾸지 말고 실제 상담원처럼 자연스럽게 읽는다. "
            "함수 결과에 없는 세부 정보(옵션, 가격, 추가 항목 등)를 먼저 나서서 "
            "설명하지 않는다 - 사용자가 묻지 않은 정보는 한 번에 다 말하지 말고, "
            "통화이므로 한 번에 한 가지만 짧게 묻거나 답한다. "
            "사용자에게 AI, 함수, 시스템 같은 내부 구현 단어를 말하지 않는다."
        ),
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.6,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 700,
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
        # required는 매 turn마다 query_agent 호출을 강제해, 에코/잡음으로 생긴
        # 허위 turn에도 모델이 message를 지어내 함수를 호출하게 만든다(사용자가
        # 말하지 않은 내용으로 AI가 혼자 대화를 이어가는 증상의 원인). instructions
        # 에 이미 "의미 있는 발화가 아니면 호출하지 마라"를 명시했으므로 auto로
        # 바꿔도 실제 발화에는 정상적으로 호출된다.
        "tool_choice": "auto",
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
