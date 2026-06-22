import json
import unittest

from app.api.chat import build_trace_detail, sse_event


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


if __name__ == "__main__":
    unittest.main()
