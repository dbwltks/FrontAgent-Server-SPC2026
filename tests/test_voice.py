import unittest

from unittest.mock import patch

from app.api.voice import build_realtime_session_config, get_voice_mode


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


if __name__ == "__main__":
    unittest.main()
