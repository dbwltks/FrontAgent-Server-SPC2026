import unittest

from unittest.mock import patch

import io
import wave

from app.api.voice import (
    ELEVENLABS_PCM_RATE,
    _pcm16_to_wav,
    build_realtime_session_config,
    get_voice_mode,
    normalize_text_for_korean_speech,
    resolve_tts_config,
    split_tts_segments,
    tts_log_fields,
)


class VoiceRealtimeTests(unittest.TestCase):
    def test_realtime_session_delegates_every_turn_to_agent(self):
        session = build_realtime_session_config()

        self.assertEqual(session["type"], "realtime")
        self.assertEqual(session["tool_choice"], "required")
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


if __name__ == "__main__":
    unittest.main()
