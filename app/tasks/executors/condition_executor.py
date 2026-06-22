from typing import Any

from app.tasks.edge_evaluator import get_value_by_path
from app.tasks.memory import TaskMemory
from app.tasks.types import ExecutorResult


def _evaluate_condition(
    actual_value: Any,
    operator: str,
    expected_value: Any,
) -> bool:
    if operator == "equals":
        return actual_value == expected_value

    if operator == "not_equals":
        return actual_value != expected_value

    if operator == "exists":
        return actual_value is not None

    if operator == "not_exists":
        return actual_value is None

    if operator == "contains":
        if actual_value is None:
            return False
        return expected_value in actual_value

    if operator == "greater_than":
        if actual_value is None or expected_value is None:
            return False
        return actual_value > expected_value

    if operator == "less_than":
        if actual_value is None or expected_value is None:
            return False
        return actual_value < expected_value

    if operator == "in":
        if not isinstance(expected_value, list):
            return False
        return actual_value in expected_value

    if operator == "not_in":
        if not isinstance(expected_value, list):
            return False
        return actual_value not in expected_value

    return False


def execute_condition_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
) -> ExecutorResult:
    config = node.get("config") or {}

    variable_path = config.get("variable")
    operator = config.get("operator") or config.get("condition_type") or "equals"
    expected_value = config.get("value")
    save_as = config.get("save_as") or "condition_result"

    if not variable_path:
        return ExecutorResult(
            status="failed",
            message="Condition Node에 variable이 설정되어 있지 않습니다.",
            next_behavior="fail",
            error={
                "code": "CONDITION_VARIABLE_MISSING",
                "message": "config.variable is required.",
            },
        )

    actual_value = get_value_by_path(
        data=memory.to_dict(),
        path=variable_path,
    )

    result = _evaluate_condition(
        actual_value=actual_value,
        operator=operator,
        expected_value=expected_value,
    )

    return ExecutorResult(
        status="success",
        message=None,
        memory_updates={
            save_as: result,
        },
        next_behavior="evaluate_edges",
    )