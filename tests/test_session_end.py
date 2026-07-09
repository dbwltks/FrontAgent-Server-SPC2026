import unittest

from app.graph.prompt_builder import build_response_instructions
from app.graph.session_end_detection import (
    is_obvious_end_session_request,
    is_obvious_handoff_request,
    try_general_fast_path_response,
)


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


class HandoffDetectionTests(unittest.TestCase):
    def test_detects_handoff_phrases(self):
        self.assertTrue(is_obvious_handoff_request("상담원 연결해 주세요"))
        self.assertTrue(is_obvious_handoff_request("사람이랑 통화하고 싶어요"))
        self.assertTrue(is_obvious_handoff_request("직원 불러주세요"))

    def test_ignores_unrelated_messages(self):
        self.assertFalse(is_obvious_handoff_request("가격이 얼마예요?"))
        self.assertFalse(is_obvious_handoff_request("김민수요"))
        self.assertFalse(is_obvious_handoff_request("예약 취소해 주세요"))


class GeneralFastPathTests(unittest.TestCase):
    def test_greeting_fast_path(self):
        self.assertEqual(
            try_general_fast_path_response("안녕하세요"),
            "안녕하세요! 무엇을 도와드릴까요?",
        )
        self.assertEqual(
            try_general_fast_path_response("안녕"),
            "안녕하세요! 무엇을 도와드릴까요?",
        )

    def test_greeting_with_history(self):
        msg = try_general_fast_path_response("안녕하세요", has_prior_assistant_turn=True)
        self.assertIsNotNone(msg)
        self.assertIn("말씀해", msg)

    def test_thanks_and_ack_fast_path(self):
        self.assertIsNotNone(try_general_fast_path_response("감사합니다"))
        self.assertEqual(try_general_fast_path_response("네"), "네, 알겠습니다.")

    def test_reservation_not_fast_path(self):
        self.assertIsNone(try_general_fast_path_response("청소 예약해줘"))
        self.assertIsNone(try_general_fast_path_response("얼마예요?"))
        self.assertIsNone(try_general_fast_path_response("어떤 서비스 있어요?"))


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
        self.assertIn("추가 질문이나", prompt)
        self.assertIn("하지 않는다", prompt)

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
