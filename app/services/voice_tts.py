import io
import logging
import wave

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services.voice_korean_text import normalize_text_for_korean_speech

logger = logging.getLogger(__name__)

OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
# ElevenLabs는 raw PCM을 주므로 이 샘플레이트로 받아 WAV 헤더를 씌워 통일한다.
ELEVENLABS_PCM_RATE = 24000
# wav(PCM)는 청크 경계에 인코더 패딩/갭이 없어 문장 단위 순차 재생이 안정적이다.
TTS_RESPONSE_FORMAT = "wav"
TTS_CONTENT_TYPE = "audio/wav"
# 문장 청크 TTS를 동시에 몇 개까지 합성할지. 첫 오디오는 빨리, OpenAI 동시 호출은 제한.
TTS_MAX_CONCURRENCY = 3


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
