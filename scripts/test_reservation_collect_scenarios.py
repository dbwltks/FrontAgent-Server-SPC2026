#!/usr/bin/env python3
"""ask_reservation_details LLM 시나리오 테스트 (다양한 입력 패턴)."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tasks.edge_evaluator import evaluate_condition_expression
from app.tasks.executors.instruction_executor import execute_instruction_node
from app.tasks.flow_generator import load_task_flow_template
from app.tasks.memory import TaskMemory


@dataclass
class Scenario:
    name: str
    memory: dict
    user_message: str
    expect_complete: bool | None = None
    must_have: dict = field(default_factory=dict)
    must_not_ask: list[str] = field(default_factory=list)


def _collect_node() -> dict:
    template = load_task_flow_template("reservation_create")
    return next(n for n in template["nodes"] if n["node_key"] == "ask_reservation_details")


def _branch_condition(node: dict) -> str:
    return node["config"]["branch_condition"]


def _base_memory(**overrides) -> dict:
    memory = {
        "service_item_id": "demo-service",
        "service_item_resolve_result": {
            "service_item": {"raw_payload": {"requires_party_size": False}},
        },
    }
    memory.update(overrides)
    return memory


SCENARIOS: list[Scenario] = [
    Scenario("1) 빈 memory + 성함만", {}, "홍길동이요", expect_complete=False, must_have={"customer_name": "홍길동"}),
    Scenario(
        "2) 성함 있음 + 날짜/시간 동시",
        _base_memory(customer_name="홍길동"),
        "7월10일 14시요",
        expect_complete=False,
        must_have={"reservation_date": "*", "reservation_time": "14:00"},
        must_not_ask=["예약 시간", "예약하실 날짜"],
    ),
    Scenario(
        "3) 성함/날짜/시간 있음 + 연락처",
        _base_memory(
            customer_name="홍길동",
            reservation_date="2026-07-10",
            reservation_time="14:00",
        ),
        "01012345678이요",
        expect_complete=True,
        must_have={"customer_phone": "010"},
    ),
    Scenario(
        "4) 한 번에 전부",
        {},
        "홍길동이고 7월10일 14시에 연락처는 01012345678이요",
        expect_complete=True,
        must_have={
            "customer_name": "홍",
            "reservation_time": "14:00",
            "customer_phone": "010",
        },
    ),
    Scenario(
        "5) 이미 다 채움 → 즉시 완료",
        _base_memory(
            customer_name="홍길동",
            reservation_date="2026-07-10",
            reservation_time="14:00",
            customer_phone="010-1234-5678",
        ),
        "네",
        expect_complete=True,
    ),
    Scenario(
        "6) 날짜만 (시간 없음)",
        _base_memory(customer_name="홍길동"),
        "7월10일이요",
        expect_complete=False,
        must_have={"reservation_date": "*"},
        must_not_ask=["성함"],
    ),
    Scenario(
        "7) 내일 오후 3시",
        _base_memory(customer_name="홍길동"),
        "내일 오후 3시요",
        expect_complete=False,
        must_have={"reservation_time": "15:00"},
        must_not_ask=["예약 시간"],
    ),
]


def _check_must_have(merged: dict, must_have: dict) -> list[str]:
    errors: list[str] = []
    for key, expected in must_have.items():
        actual = merged.get(key)
        if expected == "*":
            if not actual:
                errors.append(f"missing {key}")
        elif expected not in str(actual or ""):
            errors.append(f"{key} expected {expected!r}, got {actual!r}")
    return errors


async def run_scenarios() -> int:
    node = _collect_node()
    branch = _branch_condition(node)
    passed = 0
    failed = 0

    print("=== ask_reservation_details LLM 시나리오 테스트 ===\n")

    for scenario in SCENARIOS:
        result = await execute_instruction_node(
            node=node,
            memory=TaskMemory(scenario.memory),
            user_message=scenario.user_message,
            is_waiting_input=True,
        )
        merged = {**scenario.memory, **result.memory_updates}
        branch_ok = evaluate_condition_expression(branch, merged)
        complete = result.next_behavior == "evaluate_edges"

        errors: list[str] = []
        if scenario.expect_complete is not None and complete != scenario.expect_complete:
            errors.append(f"complete={complete}, expected={scenario.expect_complete}")
        if scenario.expect_complete is True and not branch_ok:
            errors.append("branch_condition not satisfied")
        errors.extend(_check_must_have(merged, scenario.must_have))
        for phrase in scenario.must_not_ask:
            if result.message and phrase in result.message:
                errors.append(f"should not ask about {phrase!r}, message={result.message!r}")

        status = "PASS" if not errors else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            failed += 1

        print(f"[{status}] {scenario.name}")
        print(f"  input: {scenario.user_message!r}")
        print(f"  updates: {json.dumps(result.memory_updates, ensure_ascii=False)}")
        print(f"  message: {result.message!r}")
        print(f"  next: {result.next_behavior}, branch_ok={branch_ok}")
        if errors:
            print(f"  errors: {errors}")
        print()

    print(f"결과: {passed} passed, {failed} failed / {len(SCENARIOS)} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_scenarios()))
