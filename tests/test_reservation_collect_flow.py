import asyncio
import os
import unittest
from unittest.mock import patch

from app.tasks.edge_evaluator import evaluate_condition_expression
from app.tasks.flow_generator import load_task_flow_template
from app.tasks.function_registry import _parse_korean_reservation_datetime
from app.tasks.memory import TaskMemory


def _collect_node(template: dict) -> dict:
    return next(n for n in template["nodes"] if n["node_key"] == "ask_reservation_details")


def _branch_condition(template: dict) -> str:
    return _collect_node(template)["config"]["branch_condition"]


def _base_memory(**overrides) -> dict:
    memory = {
        "service_item_id": "svc-1",
        "service_item_resolve_result": {
            "service_item": {"raw_payload": {"requires_party_size": False}},
        },
    }
    memory.update(overrides)
    return memory


class ReservationCollectTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = load_task_flow_template("reservation_create")

    def test_single_collect_node_on_main_path(self):
        removed = {"ask_name", "ask_date", "ask_party_size"}
        node_keys = {n["node_key"] for n in self.template["nodes"]}
        self.assertIn("ask_reservation_details", node_keys)
        self.assertFalse(removed & node_keys)

    def test_resolve_service_item_goes_to_collect_node(self):
        resolve = next(n for n in self.template["nodes"] if n["node_key"] == "resolve_service_item")
        self.assertEqual(resolve["config"]["next_node_key"], "ask_reservation_details")

    def test_collect_node_loops_until_complete(self):
        node = _collect_node(self.template)
        self.assertEqual(node["config"]["fallback_node_key"], "ask_reservation_details")
        self.assertEqual(node["config"]["branch_node_key"], "check_availability")


class ReservationCollectBranchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.condition = _branch_condition(load_task_flow_template("reservation_create"))

    def test_incomplete_when_only_name(self):
        memory = _base_memory(customer_name="홍길동")
        self.assertFalse(evaluate_condition_expression(self.condition, memory))

    def test_incomplete_when_date_without_time(self):
        memory = _base_memory(
            customer_name="홍길동",
            reservation_date="2026-07-10",
        )
        self.assertFalse(evaluate_condition_expression(self.condition, memory))

    def test_complete_without_party_size_when_not_required(self):
        memory = _base_memory(
            customer_name="홍길동",
            reservation_date="2026-07-10",
            reservation_time="14:00",
            customer_phone="010-1234-5678",
        )
        self.assertTrue(evaluate_condition_expression(self.condition, memory))

    def test_incomplete_when_party_size_required(self):
        memory = _base_memory(
            customer_name="홍길동",
            reservation_date="2026-07-10",
            reservation_time="14:00",
            customer_phone="010-1234-5678",
            service_item_resolve_result={
                "service_item": {"raw_payload": {"requires_party_size": True}},
            },
        )
        self.assertFalse(evaluate_condition_expression(self.condition, memory))

    def test_complete_when_party_size_required_and_present(self):
        memory = _base_memory(
            customer_name="홍길동",
            reservation_date="2026-07-10",
            reservation_time="14:00",
            customer_phone="010-1234-5678",
            party_size="2",
            service_item_resolve_result={
                "service_item": {"raw_payload": {"requires_party_size": True}},
            },
        )
        self.assertTrue(evaluate_condition_expression(self.condition, memory))


class ReservationDatetimeParserTests(unittest.TestCase):
    def test_combined_korean_date_time(self):
        parsed = _parse_korean_reservation_datetime("7월10일 14시요")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.strftime("%H:%M"), "14:00")
        self.assertTrue(parsed.strftime("%m-%d").endswith("07-10"))

    def test_tomorrow_afternoon(self):
        parsed = _parse_korean_reservation_datetime("내일 오후 3시")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.hour, 15)

    def test_time_only_phrase_without_date_returns_none(self):
        self.assertIsNone(_parse_korean_reservation_datetime("14시요"))


@unittest.skipUnless(os.getenv("RUN_LIVE_LLM_TESTS") == "1", "set RUN_LIVE_LLM_TESTS=1 to run")
class ReservationCollectLiveLlmTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.tasks.executors.instruction_executor import execute_instruction_node

        self.execute = execute_instruction_node
        self.node = _collect_node(load_task_flow_template("reservation_create"))

    async def _run_turn(self, memory: dict, user_message: str):
        result = await self.execute(
            node=self.node,
            memory=TaskMemory(memory),
            user_message=user_message,
            is_waiting_input=True,
        )
        merged = {**memory, **result.memory_updates}
        return result, merged

    async def test_name_only_then_combined_datetime(self):
        result1, mem1 = await self._run_turn({}, "홍길동이요")
        self.assertEqual(result1.next_behavior, "wait_user")
        self.assertEqual(mem1.get("customer_name"), "홍길동")

        result2, mem2 = await self._run_turn(mem1, "7월10일 14시요")
        self.assertIn(mem2.get("reservation_date"), {None, ""})  # may fill below
        if result2.next_behavior == "evaluate_edges":
            self.assertEqual(mem2.get("reservation_time"), "14:00")
            self.assertIn("010", mem2.get("customer_phone", "") or "missing")
        else:
            self.assertEqual(mem2.get("reservation_time"), "14:00")
            if result2.message:
                self.assertNotIn("예약 시간", result2.message)

    async def test_all_slots_in_one_message(self):
        result, merged = await self._run_turn(
            {},
            "홍길동이고 7월10일 14시에 가능하고 연락처는 01012345678이요",
        )
        self.assertEqual(result.next_behavior, "evaluate_edges")
        self.assertEqual(merged.get("customer_name"), "홍길동")
        self.assertEqual(merged.get("reservation_time"), "14:00")
        self.assertIn("010", merged.get("customer_phone", ""))


if __name__ == "__main__":
    unittest.main()
