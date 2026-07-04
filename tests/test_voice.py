import asyncio
import json
import unittest

from unittest.mock import AsyncMock, MagicMock, patch

import io
import wave

from app.api.organization_ai_settings import (
    ELEVENLABS_REALTIME_MODELS,
    REALTIME_MODELS_BY_PROVIDER,
    REALTIME_VOICES_BY_PROVIDER,
    normalize_update_data,
    OrganizationAISettingsUpdateRequest,
)
from app.api.voice import (
    ELEVENLABS_PCM_RATE,
    _pcm16_to_wav,
    build_realtime_session_config,
    create_elevenlabs_realtime_session,
    get_voice_mode,
    normalize_text_for_korean_speech,
    resolve_elevenlabs_agent_id,
    resolve_realtime_provider,
    voice_config,
)
from app.services.voice_korean_text import split_tts_segments
from app.services.voice_tts import (
    ELEVENLABS_WS_CHUNK_LENGTH_SCHEDULE,
    ElevenLabsWebSocketTTS,
    resolve_tts_config,
    tts_log_fields,
)


class VoiceRealtimeTests(unittest.TestCase):
    def test_realtime_session_delegates_every_turn_to_agent(self):
        session = build_realtime_session_config()

        self.assertEqual(session["type"], "realtime")
        self.assertEqual(session["tool_choice"], "auto")
        self.assertEqual(session["tools"][0]["name"], "query_agent")
        self.assertEqual(
            session["audio"]["input"]["turn_detection"]["type"],
            "server_vad",
        )
        self.assertTrue(
            session["audio"]["input"]["turn_detection"]["interrupt_response"]
        )

    def test_invalid_voice_mode_falls_back_to_pipeline(self):
        with patch("app.api.voice.settings.voice_mode", "unknown"):
            self.assertEqual(get_voice_mode(), "pipeline")

    def test_normalizes_money_for_korean_speech(self):
        self.assertEqual(
            normalize_text_for_korean_speech("가격은 1,500원입니다."),
            "가격은 천오백 원입니다.",
        )

    def test_normalizes_time_for_korean_speech(self):
        self.assertEqual(
            normalize_text_for_korean_speech("예약 가능 시간은 15:00입니다."),
            "예약 가능 시간은 오후 세 시입니다.",
        )

    def test_normalizes_date_for_korean_speech(self):
        self.assertEqual(
            normalize_text_for_korean_speech("예약일은 2026-06-23입니다."),
            "예약일은 유월 이십삼 일입니다.",
        )

    def test_normalizes_phone_number_for_korean_speech(self):
        self.assertEqual(
            normalize_text_for_korean_speech("010-1234-5678로 연락 주세요."),
            "공일공 일이삼사 오육칠팔로 연락 주세요.",
        )

    def test_voice_config_exposes_elevenlabs_tts_fields(self):
        with patch(
            "app.api.voice.get_ai_settings",
            return_value={
                "voice_enabled": True,
                "voice_mode": "realtime",
                "voice_stt_provider": "openai",
                "voice_stt_model": "gpt-4o-transcribe",
                "voice_tts_provider": "elevenlabs",
                "elevenlabs_model": "eleven_flash_v2_5",
                "elevenlabs_voice_id": "voice_123",
                "realtime_model": "gpt-realtime-2",
                "realtime_voice": "marin",
                "voice_response_style": "friendly_short",
            },
        ):
            import asyncio

            config = asyncio.run(voice_config("org_1"))

        self.assertEqual(config["mode"], "realtime")
        self.assertEqual(config["realtime_provider"], "elevenlabs")
        self.assertEqual(config["tts_provider"], "elevenlabs")
        self.assertEqual(config["elevenlabs_model"], "eleven_flash_v2_5")
        self.assertEqual(config["elevenlabs_voice_id"], "voice_123")
        self.assertEqual(config["realtime_elevenlabs_session_url"], "/voice/realtime/elevenlabs-session")

    def test_resolves_elevenlabs_realtime_provider_from_tts_provider(self):
        self.assertEqual(resolve_realtime_provider({"voice_tts_provider": "openai"}), "openai")
        self.assertEqual(resolve_realtime_provider({"voice_tts_provider": "elevenlabs"}), "elevenlabs")

    def test_resolves_elevenlabs_realtime_provider_from_realtime_model(self):
        self.assertEqual(
            resolve_realtime_provider(
                {
                    "voice_tts_provider": "openai",
                    "realtime_model": "elevenlabs-conversational-ai",
                }
            ),
            "elevenlabs",
        )

    def test_realtime_options_include_elevenlabs_model_and_voice_endpoint_shape(self):
        self.assertIn("elevenlabs-conversational-ai", ELEVENLABS_REALTIME_MODELS)
        self.assertEqual(
            REALTIME_MODELS_BY_PROVIDER["elevenlabs"],
            ["elevenlabs-conversational-ai"],
        )
        self.assertEqual(REALTIME_VOICES_BY_PROVIDER["elevenlabs"], [])

    def test_selecting_elevenlabs_realtime_model_sets_elevenlabs_provider(self):
        data = normalize_update_data(
            OrganizationAISettingsUpdateRequest(
                realtime_model="elevenlabs-conversational-ai",
            )
        )

        self.assertEqual(data["realtime_model"], "elevenlabs-conversational-ai")
        self.assertEqual(data["voice_tts_provider"], "elevenlabs")

    def test_resolves_elevenlabs_agent_id(self):
        self.assertEqual(
            resolve_elevenlabs_agent_id({"elevenlabs_agent_id": " agent_123 "}),
            "agent_123",
        )

    def test_creates_elevenlabs_realtime_signed_url(self):
        class FakeResponse:
            is_success = True
            status_code = 200
            text = '{"signed_url":"wss://example"}'

            def json(self):
                return {"signed_url": "wss://example"}

        class FakeClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers, params):
                self.url = url
                self.headers = headers
                self.params = params
                return FakeResponse()

        with (
            patch(
                "app.api.voice.get_ai_settings",
                return_value={
                    "voice_enabled": True,
                    "voice_mode": "realtime",
                    "voice_tts_provider": "elevenlabs",
                    "elevenlabs_agent_id": "agent_123",
                },
            ),
            patch("app.api.voice.settings.elevenlabs_api_key", "secret"),
            patch("app.api.voice.httpx.AsyncClient", FakeClient),
            patch("app.api.voice.create_usage_log_background"),
        ):
            import asyncio

            response = asyncio.run(
                create_elevenlabs_realtime_session(
                    organization_id="org_1",
                    session_id="sess_1",
                )
            )

        self.assertEqual(response.provider, "elevenlabs")
        self.assertEqual(response.agent_id, "agent_123")
        self.assertEqual(response.signed_url, "wss://example")


class StreamingTtsSegmentTests(unittest.TestCase):
    def test_emits_completed_sentence_keeps_remainder(self):
        segments, remainder = split_tts_segments("안녕하세요 반갑습니다. 무엇을")
        self.assertEqual(segments, ["안녕하세요 반갑습니다."])
        self.assertEqual(remainder, " 무엇을")

    def test_waits_until_minimum_length(self):
        # 짧은 문장은 바로 끊지 않고 다음 delta를 기다린다.
        segments, remainder = split_tts_segments("네.")
        self.assertEqual(segments, [])
        self.assertEqual(remainder, "네.")

    def test_does_not_split_decimal_point(self):
        segments, remainder = split_tts_segments("적용 배율은 1.5배로 계산됩니다")
        self.assertEqual(segments, [])
        self.assertEqual(remainder, "적용 배율은 1.5배로 계산됩니다")

    def test_flush_all_emits_remaining_buffer(self):
        segments, remainder = split_tts_segments("마지막 문장입니다", flush_all=True)
        self.assertEqual(segments, ["마지막 문장입니다"])
        self.assertEqual(remainder, "")

    def test_force_splits_long_run_without_punctuation(self):
        long_text = "가" * 200
        segments, remainder = split_tts_segments(long_text)
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0]), 160)
        self.assertEqual(len(remainder), 40)


class TtsProviderTests(unittest.TestCase):
    def test_defaults_to_openai_provider(self):
        cfg = resolve_tts_config({"voice_tts_model": "gpt-4o-mini-tts", "voice_tts_voice": "marin"})
        self.assertEqual(cfg["provider"], "openai")
        self.assertEqual(tts_log_fields(cfg), ("openai", "gpt-4o-mini-tts"))

    def test_switches_to_elevenlabs_provider(self):
        cfg = resolve_tts_config(
            {
                "voice_tts_provider": "elevenlabs",
                "elevenlabs_model": "eleven_flash_v2_5",
                "elevenlabs_voice_id": "VID",
            }
        )
        self.assertEqual(cfg["provider"], "elevenlabs")
        self.assertEqual(cfg["elevenlabs_voice_id"], "VID")
        self.assertEqual(tts_log_fields(cfg), ("elevenlabs", "eleven_flash_v2_5"))

    def test_unknown_provider_falls_back_to_openai(self):
        cfg = resolve_tts_config({"voice_tts_provider": "garbage"})
        self.assertEqual(cfg["provider"], "openai")

    def test_pcm_is_wrapped_into_valid_wav(self):
        wav_bytes = _pcm16_to_wav(b"\x00\x01" * 500, ELEVENLABS_PCM_RATE)
        self.assertEqual(wav_bytes[:4], b"RIFF")
        reader = wave.open(io.BytesIO(wav_bytes), "rb")
        self.assertEqual(reader.getframerate(), ELEVENLABS_PCM_RATE)
        self.assertEqual(reader.getnchannels(), 1)
        self.assertEqual(reader.getsampwidth(), 2)


class ElevenLabsWebSocketTtsTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_connection_uses_official_generation_config(self):
        ws = ElevenLabsWebSocketTTS({"elevenlabs_voice_id": "voice_123"})
        mock_ws = AsyncMock()
        ws._ws = mock_ws

        await ws._send_payload({
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            "generation_config": {
                "chunk_length_schedule": ELEVENLABS_WS_CHUNK_LENGTH_SCHEDULE,
            },
        })

        payload = json.loads(mock_ws.send.await_args.args[0])
        self.assertEqual(payload["generation_config"]["chunk_length_schedule"], [120, 160, 250, 290])
        self.assertNotIn("try_trigger_generation", payload)

    async def test_send_text_streams_without_try_trigger_generation(self):
        ws = ElevenLabsWebSocketTTS({"elevenlabs_voice_id": "voice_123"})
        mock_ws = AsyncMock()
        ws._ws = mock_ws

        await ws.send_text("안녕하세요.")

        payload = json.loads(mock_ws.send.await_args.args[0])
        self.assertEqual(payload["text"], "안녕하세요. ")
        self.assertNotIn("try_trigger_generation", payload)

    async def test_flush_includes_text_and_flush_flag(self):
        ws = ElevenLabsWebSocketTTS({"elevenlabs_voice_id": "voice_123"})
        mock_ws = AsyncMock()
        ws._ws = mock_ws

        await ws.flush("마지막 문장입니다")

        payload = json.loads(mock_ws.send.await_args.args[0])
        self.assertEqual(payload["text"], "마지막 문장입니다 ")
        self.assertTrue(payload["flush"])


if __name__ == "__main__":
    unittest.main()
