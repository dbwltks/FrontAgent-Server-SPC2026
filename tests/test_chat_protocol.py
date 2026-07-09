import json
import unittest

from app.api.chat import build_trace_detail, sse_event


class ChatProtocolTests(unittest.TestCase):
    def test_prepare_trace_contains_intent_and_action(self):
        detail, items = build_trace_detail(
            "prepare",
            {
                "intent": "reservation",
                "next_action": "run_task",
                "task_type": "reservation_create",
            },
        )

        self.assertIn("intent=reservation", detail)
        self.assertIn("next_action=run_task", detail)
        self.assertEqual(items, [])

    def test_unknown_node_returns_empty(self):
        detail, items = build_trace_detail("unknown_node", {})
        self.assertEqual(detail, "")
        self.assertEqual(items, [])

    def test_sse_event_uses_named_event_and_json_data(self):
        event = sse_event("delta", {"delta": "안녕"})
        event_name, data_line = event.strip().splitlines()

        self.assertEqual(event_name, "event: delta")
        self.assertEqual(json.loads(data_line.removeprefix("data: ")), {"delta": "안녕"})


if __name__ == "__main__":
    unittest.main()
