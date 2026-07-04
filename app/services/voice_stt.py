import logging

import httpx
from fastapi import HTTPException, UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)

OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
CLOVA_SPEECH_LANG = "Kor"

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


def resolve_stt_model(provider: str | None, model: str | None) -> str:
    normalized_provider = (provider or "openai").strip().lower()

    if normalized_provider == "deepgram":
        cleaned = (model or "").strip()
        if cleaned.startswith("nova"):
            return cleaned
        return settings.deepgram_stt_model

    if normalized_provider == "clova":
        return "clova-speech"

    return (model or settings.voice_stt_model).strip()


async def read_audio_upload(audio: UploadFile) -> bytes:
    content = await audio.read(settings.voice_upload_max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Audio file is required")
    if len(content) > settings.voice_upload_max_bytes:
        raise HTTPException(status_code=413, detail="Audio file is too large")
    return content


async def _transcribe_clova(content: bytes) -> str:
    if not settings.clova_speech_api_url or not settings.clova_speech_api_secret:
        logger.error("CLOVA Speech API URL/secret is not configured")
        raise HTTPException(status_code=500, detail="CLOVA Speech is not configured")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                settings.clova_speech_api_url,
                params={"lang": CLOVA_SPEECH_LANG},
                headers={
                    "X-CLOVASPEECH-API-KEY": settings.clova_speech_api_secret,
                    "Content-Type": "application/octet-stream",
                },
                content=content,
            )
    except httpx.HTTPError:
        logger.exception("CLOVA Speech transcription request failed")
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    if not response.is_success:
        logger.error(
            "CLOVA Speech transcription rejected request: status=%s body=%s",
            response.status_code,
            response.text[:1000],
        )
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    return str(response.json().get("text", "")).strip()


async def _transcribe_deepgram(
    content: bytes,
    *,
    content_type: str,
    model: str,
) -> str:
    if not settings.deepgram_api_key:
        logger.error("Deepgram API key is not configured")
        raise HTTPException(status_code=500, detail="Deepgram is not configured")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                DEEPGRAM_LISTEN_URL,
                params={
                    "model": model,
                    "language": "ko",
                    "punctuate": "true",
                    "smart_format": "true",
                },
                headers={
                    "Authorization": f"Token {settings.deepgram_api_key}",
                    "Content-Type": content_type,
                },
                content=content,
            )
    except httpx.HTTPError:
        logger.exception("Deepgram transcription request failed")
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    if not response.is_success:
        logger.error(
            "Deepgram transcription rejected request: status=%s model=%s content_type=%s bytes=%s body=%s",
            response.status_code,
            model,
            content_type,
            len(content),
            response.text[:1000],
        )
        if response.status_code == 400:
            raise HTTPException(
                status_code=422,
                detail="Uploaded audio could not be processed",
            )
        raise HTTPException(status_code=502, detail="Voice transcription failed")

    payload = response.json()
    channels = (payload.get("results") or {}).get("channels") or []
    if not channels:
        return ""

    alternatives = channels[0].get("alternatives") or []
    if not alternatives:
        return ""

    return str(alternatives[0].get("transcript", "")).strip()


async def transcribe_audio_content(
    *,
    content: bytes,
    filename: str | None,
    content_type: str | None,
    model: str,
    provider: str = "openai",
) -> tuple[str, dict]:
    upload_filename, upload_content_type = normalize_transcription_upload(content_type)
    resolved_model = resolve_stt_model(provider, model)
    logger.info(
        "transcription upload received: filename=%s content_type=%s normalized_filename=%s normalized_content_type=%s bytes=%s model=%s provider=%s",
        filename,
        content_type,
        upload_filename,
        upload_content_type,
        len(content),
        resolved_model,
        provider,
    )

    metadata = {
        "filename": filename,
        "content_type": content_type,
        "normalized_filename": upload_filename,
        "normalized_content_type": upload_content_type,
        "resolved_model": resolved_model,
    }

    if provider == "clova":
        text = await _transcribe_clova(content)
        if not text:
            raise HTTPException(status_code=422, detail="No speech was detected")
        return text, metadata

    if provider == "deepgram":
        text = await _transcribe_deepgram(
            content,
            content_type=upload_content_type,
            model=resolved_model,
        )
        if not text:
            raise HTTPException(status_code=422, detail="No speech was detected")
        return text, metadata

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            openai_response = await client.post(
                OPENAI_TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={
                    "model": resolved_model,
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

    return text, metadata
