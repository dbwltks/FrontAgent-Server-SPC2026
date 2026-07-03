import unittest

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from app.graph.checkpoint_state import (
    build_checkpoint_message_updates,
    merge_turn_message_updates,
    slim_channel_values_for_checkpoint,
)
from app.graph.graph_runtime import build_initial_state, thread_id_for


class GraphRuntimeTests(unittest.TestCase):
    def test_initial_state_does_not_reset_checkpointed_task_state(self):
        state = build_initial_state(
            organization_id="org-id",
            session_id="session-id",
            user_message="예약을 이어서 진행할게요",
        )

        self.assertNotIn("active_task", state)
        self.assertNotIn("task_step", state)

    def test_thread_id_is_scoped_by_organization_and_session(self):
        self.assertEqual(
            thread_id_for("org-id", "session-id"),
            "org-id:session-id",
        )

    def test_merge_turn_message_updates_keeps_user_and_assistant(self):
        merged = merge_turn_message_updates(
            {"messages": [{"role": "user", "content": "안녕"}]},
            {"messages": [{"role": "assistant", "content": "안녕하세요"}]},
        )
        self.assertEqual(len(merged), 2)

    def test_slim_channel_values_clears_ephemeral_fields(self):
        slim = slim_channel_values_for_checkpoint(
            {
                "messages": [{"role": "assistant", "content": "답"}],
                "knowledge_context": [{"content": "big"}],
                "used_knowledge": [{"source_id": "1"}],
                "active_task": "reservation",
                "task_step": "ask_service",
                "final_response": "답",
            }
        )
        self.assertEqual(slim["knowledge_context"], [])
        self.assertEqual(slim["used_knowledge"], [])
        self.assertIsNone(slim["final_response"])
        self.assertEqual(slim["active_task"], "reservation")
        self.assertEqual(slim["task_step"], "ask_service")

    def test_build_checkpoint_message_updates_trims_old_messages(self):
        prior = [
            HumanMessage(content="old", id="m1"),
            AIMessage(content="old-ai", id="m2"),
        ]
        updates = build_checkpoint_message_updates(
            prior,
            [{"role": "user", "content": "new"}],
            max_messages=2,
        )
        self.assertEqual(len(updates), 2)
        self.assertIsInstance(updates[0], RemoveMessage)
        self.assertEqual(updates[1]["content"], "new")


if __name__ == "__main__":
    unittest.main()
