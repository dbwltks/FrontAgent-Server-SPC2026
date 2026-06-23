import hashlib
import json
import logging

import httpx
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.core.config import settings
from app.repositories.ai_usage_repo import create_usage_log_background
from app.repositories.organization_ai_settings_repo import get_ai_settings


router = APIRouter(prefix="/voice", tags=["Voice"])
logger = logging.getLogger(__name__)
OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
REALTIME_ERROR_MESSAGE = "Realtime voice connection failed"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"


class SpeechRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    organization_id: str | None = None
    session_id: str | None = None
    channel: str = "web_call"


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

    content = await audio.read(settings.voice_upload_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Audio file is required")
    if len(content) > settings.voice_upload_max_bytes:
        raise HTTPException(status_code=413, detail="Audio file is too large")

    files = {
        "file": (
            audio.filename or "utterance.webm",
            content,
            audio.content_type or "audio/webm",
        )
    }
    data = {
        "model": stt_model,
        "language": "ko",
        "response_format": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            openai_response = await client.post(
                OPENAI_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data=data,
                files=files,
            )
    except httpx.HTTPError:
        logger.exception("OpenAI transcription request failed")
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    if not openai_response.is_success:
        logger.error(
            "OpenAI transcription rejected request: status=%s body=%s",
            openai_response.status_code,
            openai_response.text[:500],
        )
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    text = str(openai_response.json().get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=422, detail="No speech was detected")

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
                "metadata": {
                    "filename": audio.filename,
                    "content_type": audio.content_type,
                },
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
    tts_model = (
        ai_settings.get("voice_tts_model")
        if ai_settings
        else settings.voice_tts_model
    )
    tts_voice = (
        ai_settings.get("voice_tts_voice")
        if ai_settings
        else settings.voice_tts_voice
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            openai_response = await client.post(
                OPENAI_SPEECH_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": tts_model,
                    "voice": tts_voice,
                    "input": payload.text,
                    "response_format": "mp3",
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

    if payload.organization_id:
        create_usage_log_background(
            {
                "organization_id": payload.organization_id,
                "session_id": payload.session_id,
                "channel": payload.channel,
                "feature": "tts",
                "provider": "openai",
                "model": tts_model,
                "audio_bytes": len(openai_response.content),
                "text_chars": len(payload.text),
                "metadata": {
                    "voice": tts_voice,
                    "response_format": "mp3",
                },
            }
        )

    return Response(content=openai_response.content, media_type="audio/mpeg")


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
            "너는 Front Agent의 음성 입출력 인터페이스다. "
            "사용자가 말할 때마다 query_agent 함수를 정확히 한 번 호출하고, "
            "message에는 사용자의 발화를 한국어 텍스트로 전달한다. "
            "함수 결과를 받기 전에는 자체 지식으로 답하지 않는다. "
            "함수 결과를 받은 뒤에는 내용을 추가하거나 바꾸지 말고 자연스럽게 읽는다."
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
