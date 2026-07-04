import unittest

from app.tasks.memory import TaskMemory
from app.tasks.service_selection import (
    _parse_reservation_selection_number,
    build_cancel_selection_message,
    build_lookup_result_message,
    try_fast_path_ask_cancel_number_instruction,
)


class LookupResultTests(unittest.TestCase):
    def test_build_lookup_result_message(self):
        message = build_lookup_result_message(
            {
                "reservation_options": [
                    {"label": "1. 화장실 청소 / 07/03 11:00 / 홍길동 (requested)"},
                ]
            }
        )
        self.assertIn("1. 화장실 청소", message or "")


class CancelSelectionTests(unittest.TestCase):
    def test_parse_selection_ignores_phone(self):
        self.assertIsNone(_parse_reservation_selection_number("010-1234-5678"))
        self.assertEqual(_parse_reservation_selection_number("1번"), 1)
        self.assertEqual(_parse_reservation_selection_number("2"), 2)

    def test_build_cancel_selection_message(self):
        message = build_cancel_selection_message(
            {
                "cancelable_options": [
                    {"label": "1. 화장실 청소 / 07/03 11:00 / 홍길동 (requested)"},
                    {"label": "2. 주방 청소 / 07/04 09:00 / 홍길동 (requested)"},
                ]
            }
        )
        self.assertIn("1. 화장실 청소", message or "")
        self.assertIn("2. 주방 청소", message or "")

    def test_fast_path_lists_options_after_lookup(self):
        node = {"node_key": "ask_cancel_number", "config": {}}
        memory = TaskMemory(
            {
                "cancelable_options": [
                    {"label": "1. 화장실 청소 / 07/03 11:00 / 홍길동 (requested)"},
                ]
            }
        )
        result = try_fast_path_ask_cancel_number_instruction(
            node=node,
            memory=memory,
            user_message="010-1234-5678",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.next_behavior, "wait_user")
        self.assertIn("1. 화장실 청소", result.message or "")

    def test_fast_path_accepts_selection_number(self):
        node = {"node_key": "ask_cancel_number", "config": {}}
        memory = TaskMemory(
            {
                "cancelable_options": [
                    {"label": "1. 화장실 청소 / 07/03 11:00 / 홍길동 (requested)"},
                    {"label": "2. 주방 청소 / 07/04 09:00 / 홍길동 (requested)"},
                ]
            }
        )
        result = try_fast_path_ask_cancel_number_instruction(
            node=node,
            memory=memory,
            user_message="2번",
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.next_behavior, "evaluate_edges")
        self.assertEqual(result.memory_updates.get("selected_reservation_number"), "2")


if __name__ == "__main__":
    unittest.main()
