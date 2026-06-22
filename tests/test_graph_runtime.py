import unittest

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


if __name__ == "__main__":
    unittest.main()
