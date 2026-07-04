import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.services.voice_stt import resolve_stt_model, transcribe_audio_content


class ResolveSttModelTests(unittest.TestCase):
    def test_deepgram_defaults_to_nova_3(self):
        with patch("app.services.voice_stt.settings.deepgram_stt_model", "nova-3"):
            self.assertEqual(resolve_stt_model("deepgram", None), "nova-3")
            self.assertEqual(
                resolve_stt_model("deepgram", "gpt-4o-mini-transcribe"),
                "nova-3",
            )

    def test_deepgram_keeps_nova_model(self):
        self.assertEqual(resolve_stt_model("deepgram", "nova-2-general"), "nova-2-general")

    def test_clova_uses_fixed_model(self):
        self.assertEqual(resolve_stt_model("clova", "anything"), "clova-speech")


class DeepgramTranscribeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_deepgram_returns_transcript(self):
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "results": {
                "channels": [
                    {"alternatives": [{"transcript": "7월 2일 14시요"}]},
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.voice_stt.settings.deepgram_api_key", "test-key"):
            with patch("app.services.voice_stt.httpx.AsyncClient", return_value=mock_client):
                text, metadata = await transcribe_audio_content(
                    content=b"audio-bytes",
                    filename="utterance.webm",
                    content_type="audio/webm",
                    model="nova-3",
                    provider="deepgram",
                )

        self.assertEqual(text, "7월 2일 14시요")
        self.assertEqual(metadata["resolved_model"], "nova-3")
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.await_args.kwargs
        self.assertEqual(call_kwargs["params"]["model"], "nova-3")
        self.assertEqual(call_kwargs["params"]["language"], "ko")
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Token test-key")

    async def test_transcribe_deepgram_requires_api_key(self):
        with patch("app.services.voice_stt.settings.deepgram_api_key", ""):
            with self.assertRaises(HTTPException) as ctx:
                await transcribe_audio_content(
                    content=b"audio-bytes",
                    filename="utterance.webm",
                    content_type="audio/webm",
                    model="nova-3",
                    provider="deepgram",
                )

        self.assertEqual(ctx.exception.status_code, 500)
