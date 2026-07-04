import asyncio
import base64
import io
import json
import logging
import wave
from typing import AsyncIterator

import httpx
import websockets
from fastapi import HTTPException

from app.core.config import settings
from app.services.voice_korean_text import normalize_text_for_korean_speech

logger = logging.getLogger(__name__)

OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
# ElevenLabs는 raw PCM을 주므로 이 샘플레이트로 받아 WAV 헤더를 씌워 통일한다.
ELEVENLABS_PCM_RATE = 24000
# ElevenLabs WebSocket 공식 기본값. 문장 경계 flush와 함께 쓰면 짧은 답변도 즉시 생성된다.
ELEVENLABS_WS_CHUNK_LENGTH_SCHEDULE = [120, 160, 250, 290]
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


def _elevenlabs_configured(tts: dict) -> bool:
    return bool(settings.elevenlabs_api_key) and bool(tts.get("elevenlabs_voice_id"))


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

    문장 전체 합성이 끝날 때까지 기다리는 비스트리밍 경로다. 첫 오디오 바이트를
    최대한 빨리 흘려보내고 싶으면 stream_speech_content를 쓴다.
    """
    speech_text = normalize_text_for_korean_speech(text)
    if tts.get("provider") == "elevenlabs" and _elevenlabs_configured(tts):
        return await _synthesize_elevenlabs(speech_text, tts)
    if tts.get("provider") == "elevenlabs":
        logger.warning("ElevenLabs not configured, falling back to OpenAI TTS")
    return await _synthesize_openai(
        speech_text,
        tts.get("model") or settings.voice_tts_model,
        tts.get("voice") or settings.voice_tts_voice,
        response_format,
    )


# ElevenLabs 스트리밍 PCM을 이 정도 크기로 모아 WAV 청크 하나로 흘려보낸다.
# 너무 작으면 청크 개수(=클라이언트 디코딩 오버헤드)가 늘고, 너무 크면 첫 청크가
# 늦어진다. 24kHz 16bit mono 기준 약 0.2초 분량.
_ELEVENLABS_STREAM_CHUNK_BYTES = ELEVENLABS_PCM_RATE * 2 // 5


async def _stream_elevenlabs(speech_text: str, tts: dict) -> AsyncIterator[bytes]:
    api_key = settings.elevenlabs_api_key
    voice_id = tts.get("elevenlabs_voice_id")
    model_id = tts.get("elevenlabs_model") or settings.elevenlabs_model

    if not api_key:
        logger.error("ElevenLabs API key is not configured")
        raise HTTPException(status_code=500, detail="ElevenLabs is not configured")
    if not voice_id:
        logger.error("ElevenLabs voice_id is not configured")
        raise HTTPException(status_code=500, detail="ElevenLabs voice is not configured")

    pcm_buffer = bytearray()

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{ELEVENLABS_TTS_URL}/{voice_id}/stream",
                params={
                    "output_format": f"pcm_{ELEVENLABS_PCM_RATE}",
                    # 0(기본, 미최적화)~4(최대 최적화). 4는 발음 정확도가 다소
                    # 떨어질 수 있으나 web_call의 첫 오디오 지연이 우선이라 채택한다.
                    "optimize_streaming_latency": 4,
                },
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={"text": speech_text, "model_id": model_id},
            ) as response:
                if not response.is_success:
                    body = await response.aread()
                    logger.error(
                        "ElevenLabs stream rejected request: status=%s body=%s",
                        response.status_code,
                        body[:500],
                    )
                    raise HTTPException(status_code=502, detail="Speech generation failed")

                async for raw_chunk in response.aiter_bytes():
                    pcm_buffer.extend(raw_chunk)
                    while len(pcm_buffer) >= _ELEVENLABS_STREAM_CHUNK_BYTES:
                        chunk = bytes(pcm_buffer[:_ELEVENLABS_STREAM_CHUNK_BYTES])
                        del pcm_buffer[:_ELEVENLABS_STREAM_CHUNK_BYTES]
                        yield _pcm16_to_wav(chunk, ELEVENLABS_PCM_RATE)
    except httpx.HTTPError:
        logger.exception("ElevenLabs speech stream failed")
        raise HTTPException(status_code=502, detail="Speech generation failed")

    if pcm_buffer:
        yield _pcm16_to_wav(bytes(pcm_buffer), ELEVENLABS_PCM_RATE)


class ElevenLabsWebSocketTTS:
    """
    ElevenLabs WebSocket TTS 세션 (공식 realtime TTS 가이드 패턴).

    - InitializeConnection: generation_config.chunk_length_schedule (기본값)
    - LLM delta는 try_trigger_generation 없이 스트리밍
    - 문장/턴 경계에서 flush: true 로 버퍼 강제 생성
    - 종료: {"text": ""} (CloseConnection)
    """

    def __init__(self, tts: dict) -> None:
        self._tts = tts
        self._api_key = settings.elevenlabs_api_key
        self._voice_id = tts.get("elevenlabs_voice_id") or settings.elevenlabs_voice_id
        self._model_id = tts.get("elevenlabs_model") or settings.elevenlabs_model
        self._ws = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._receiver_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    async def __aenter__(self) -> "ElevenLabsWebSocketTTS":
        if not self._api_key or not self._voice_id:
            raise HTTPException(status_code=500, detail="ElevenLabs is not configured")

        url = (
            ELEVENLABS_WS_URL.format(voice_id=self._voice_id)
            + f"?model_id={self._model_id}"
            + f"&output_format=pcm_{ELEVENLABS_PCM_RATE}"
        )
        self._ws = await websockets.connect(
            url,
            additional_headers={"xi-api-key": self._api_key},
        )
        # InitializeConnection: 공식 문서의 generation_config + voice_settings
        await self._send_payload({
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            "generation_config": {
                "chunk_length_schedule": ELEVENLABS_WS_CHUNK_LENGTH_SCHEDULE,
            },
        })
        # 오디오 수신 백그라운드 태스크
        self._receiver_task = asyncio.create_task(self._receive_loop())
        return self

    async def __aexit__(self, *_) -> None:
        if self._ws:
            try:
                # 종료 신호 전송 후 수신 루프가 끝나길 기다린다
                await self._send_payload({"text": ""})
            except Exception:
                pass
        # 수신 루프 완료 대기 (루프 내에서 None을 queue에 넣음)
        if self._receiver_task:
            try:
                await asyncio.wait_for(self._receiver_task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _receive_loop(self) -> None:
        pcm_buffer = bytearray()
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("audio"):
                    pcm_buffer.extend(base64.b64decode(msg["audio"]))
                    while len(pcm_buffer) >= _ELEVENLABS_STREAM_CHUNK_BYTES:
                        chunk = bytes(pcm_buffer[:_ELEVENLABS_STREAM_CHUNK_BYTES])
                        del pcm_buffer[:_ELEVENLABS_STREAM_CHUNK_BYTES]
                        await self._audio_queue.put(_pcm16_to_wav(chunk, ELEVENLABS_PCM_RATE))
                if msg.get("isFinal"):
                    break
        except Exception:
            logger.exception("ElevenLabs WebSocket receive failed")
        finally:
            if pcm_buffer:
                await self._audio_queue.put(_pcm16_to_wav(bytes(pcm_buffer), ELEVENLABS_PCM_RATE))
            await self._audio_queue.put(None)

    async def _send_payload(self, payload: dict) -> None:
        if not self._ws:
            return
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    @staticmethod
    def _format_send_text(text: str) -> str:
        return text if text.endswith(" ") else f"{text} "

    async def send_text(self, text: str, *, flush: bool = False) -> None:
        """LLM delta/문장 텍스트를 WebSocket으로 전송한다. flush=True면 버퍼를 즉시 생성."""
        if not self._ws:
            return
        if not text.strip() and not flush:
            return
        payload: dict = {"text": self._format_send_text(text) if text.strip() else " "}
        if flush:
            payload["flush"] = True
        await self._send_payload(payload)

    async def flush(self, text: str = "") -> None:
        """문장/턴 경계에서 버퍼에 쌓인 텍스트를 강제 생성한다."""
        await self.send_text(text, flush=True)

    async def iter_audio(self) -> AsyncIterator[bytes]:
        """생성된 오디오 WAV 청크를 순서대로 yield한다."""
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            yield chunk


async def stream_speech_content(
    *,
    text: str,
    tts: dict,
    response_format: str = TTS_RESPONSE_FORMAT,
) -> AsyncIterator[bytes]:
    """
    가능하면 진짜 스트리밍(ElevenLabs)으로 오디오 청크를 흘려보낸다.
    OpenAI는 스트리밍 청크 분할을 지원하지 않으므로 합성이 끝난 통짜 결과를
    한 번에 yield해 호출부가 provider와 무관하게 같은 인터페이스로 쓸 수 있게 한다.
    """
    speech_text = normalize_text_for_korean_speech(text)

    if tts.get("provider") == "elevenlabs":
        async for chunk in _stream_elevenlabs(speech_text, tts):
            yield chunk
        return

    yield await _synthesize_openai(
        speech_text,
        tts.get("model") or settings.voice_tts_model,
        tts.get("voice") or settings.voice_tts_voice,
        response_format,
    )
