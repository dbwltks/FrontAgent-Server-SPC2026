import unittest

from app.graph.prompt_builder import build_response_instructions
from app.graph.session_end_detection import is_obvious_end_session_request


class SessionEndDetectionTests(unittest.TestCase):
    def test_detects_call_end_phrases(self):
        self.assertTrue(is_obvious_end_session_request("통화 종료해 주세요"))
        self.assertTrue(is_obvious_end_session_request("전화 끊어줘"))

    def test_detects_chat_end_phrases(self):
        self.assertTrue(is_obvious_end_session_request("채팅 종료할게요"))
        self.assertTrue(is_obvious_end_session_request("대화 여기까지"))

    def test_ignores_unrelated_messages(self):
        self.assertFalse(is_obvious_end_session_request("가격이 얼마예요?"))
        self.assertFalse(is_obvious_end_session_request("예약 취소해 주세요"))


class PromptBuilderEndSessionTests(unittest.TestCase):
    def test_voice_prompt_includes_end_session_guidance(self):
        prompt = build_response_instructions(
            intent="end_session",
            knowledge_context=[],
            use_knowledge=False,
            rules=[],
            channel="web_call",
            should_end_session=True,
        )
        self.assertIn("[통화 종료]", prompt)
        self.assertIn("더 도와드릴 일이 있으신가요", prompt)

    def test_chat_prompt_includes_end_session_guidance(self):
        prompt = build_response_instructions(
            intent="end_session",
            knowledge_context=[],
            use_knowledge=False,
            rules=[],
            channel="web_chat",
            should_end_session=True,
        )
        self.assertIn("[상담 종료]", prompt)
        self.assertIn("채팅을 종료합니다", prompt)


if __name__ == "__main__":
    unittest.main()
