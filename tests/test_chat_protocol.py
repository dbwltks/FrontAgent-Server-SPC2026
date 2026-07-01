import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.voice import router as voice_router
from app.api.chat import build_trace_detail, sse_event


def _client_with_chat_router() -> TestClient:
    app = FastAPI()
    app.include_router(chat_router)
    return TestClient(app)


def _client_with_conversations_router() -> TestClient:
    app = FastAPI()
    app.include_router(conversations_router)
    return TestClient(app)


def _client_with_voice_router() -> TestClient:
    app = FastAPI()
    app.include_router(voice_router)
    return TestClient(app)


class ChatProtocolTests(unittest.TestCase):
    def test_rule_trace_contains_only_rule_instruction_fields(self):
        detail, items = build_trace_detail(
            "rule",
            {
                "rules": [
                    {
                        "name": "존댓말",
                        "instruction": "항상 존댓말을 사용한다.",
                    }
                ]
            },
        )

        self.assertEqual(detail, "활성 규칙 1개를 응답 지시문에 반영")
        self.assertEqual(
            items,
            [{"name": "존댓말", "instruction": "항상 존댓말을 사용한다."}],
        )

    def test_sse_event_uses_named_event_and_json_data(self):
        event = sse_event("delta", {"delta": "안녕"})
        event_name, data_line = event.strip().splitlines()

        self.assertEqual(event_name, "event: delta")
        self.assertEqual(json.loads(data_line.removeprefix("data: ")), {"delta": "안녕"})

    @patch("app.api.chat.get_conversation_by_session")
    def test_chat_rejects_closed_session_id_before_agent_run(self, get_conversation_by_session):
        get_conversation_by_session.return_value = {
            "id": "conversation-id",
            "organization_id": "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
            "session_id": "closed-session",
            "status": "closed",
        }

        response = _client_with_chat_router().post(
            "/chat",
            json={
                "organization_id": "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
                "session_id": "closed-session",
                "message": "다시 문의할게요",
                "stream": False,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "SESSION_CLOSED")
        self.assertTrue(response.json()["detail"]["new_session_required"])

    @patch("app.api.chat.get_conversation_by_session")
    def test_stream_chat_rejects_closed_session_id_before_sse_starts(self, get_conversation_by_session):
        get_conversation_by_session.return_value = {
            "id": "conversation-id",
            "session_id": "closed-session",
            "status": "closed",
        }

        response = _client_with_chat_router().post(
            "/chat",
            json={
                "organization_id": "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
                "session_id": "closed-session",
                "message": "다시 문의할게요",
                "stream": True,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["detail"]["new_session_required"])

    @patch("app.api.conversations.invalidate_conversation_cache")
    @patch("app.api.conversations.TaskRepository")
    @patch("app.api.conversations.close_conversation")
    def test_close_conversation_cancels_active_task_session(
        self,
        close_conversation,
        task_repository_cls,
        invalidate_conversation_cache,
    ):
        close_conversation.return_value = {
            "id": "conversation-id",
            "organization_id": "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
            "session_id": "chat-session",
            "status": "closed",
        }
        task_repository = MagicMock()
        task_repository_cls.return_value = task_repository

        response = _client_with_conversations_router().patch(
            "/conversations/conversation-id/close",
            params={"organization_id": "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da"},
        )

        self.assertEqual(response.status_code, 200)
        task_repository.cancel_active_sessions.assert_called_once_with(
            organization_id="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
            session_id="chat-session",
        )
        invalidate_conversation_cache.assert_called_once_with(
            "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
            "chat-session",
        )

    @patch("app.api.voice.invalidate_conversation_cache")
    @patch("app.api.voice.TaskRepository")
    @patch("app.api.voice.end_call_conversation")
    def test_voice_call_end_is_idempotent_when_conversation_is_missing(
        self,
        end_call_conversation,
        task_repository_cls,
        invalidate_conversation_cache,
    ):
        end_call_conversation.return_value = None
        task_repository = MagicMock()
        task_repository_cls.return_value = task_repository

        response = _client_with_voice_router().post(
            "/voice/call/end",
            json={
                "organization_id": "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
                "session_id": "already-reset-session",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["already_closed"])
        task_repository.cancel_active_sessions.assert_called_once_with(
            organization_id="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
            session_id="already-reset-session",
        )
        invalidate_conversation_cache.assert_called_once_with(
            "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
            "already-reset-session",
        )


if __name__ == "__main__":
    unittest.main()
