import re
from typing import Any

from app.tasks.edge_evaluator import get_value_by_path
from app.tasks.function_registry import get_task_function
from app.tasks.memory import TaskMemory
from app.tasks.types import ExecutorResult


FULL_MEMORY_TEMPLATE_PATTERN = re.compile(r"^{{\s*memory\.([a-zA-Z0-9_.]+)\s*}}$")
PARTIAL_MEMORY_TEMPLATE_PATTERN = re.compile(r"{{\s*memory\.([a-zA-Z0-9_.]+)\s*}}")


def _resolve_memory_template_value(
    value: Any,
    memory: TaskMemory,
) -> Any:
    """
    Function Node params 안의 {{memory.xxx}} 값을 실제 memory 값으로 바꾼다.

    예:
    "{{memory.normalized_date}}" -> "2026-06-22"
    "예약일: {{memory.normalized_date}}" -> "예약일: 2026-06-22"
    """

    if isinstance(value, dict):
        return {
            key: _resolve_memory_template_value(child_value, memory)
            for key, child_value in value.items()
        }

    if isinstance(value, list):
        return [
            _resolve_memory_template_value(item, memory)
            for item in value
        ]

    if not isinstance(value, str):
        return value

    stripped_value = value.strip()
    full_match = FULL_MEMORY_TEMPLATE_PATTERN.match(stripped_value)

    if full_match:
        memory_path = f"memory.{full_match.group(1)}"
        return get_value_by_path(memory.to_dict(), memory_path)

    def replace_partial(match: re.Match) -> str:
        memory_path = f"memory.{match.group(1)}"
        resolved_value = get_value_by_path(memory.to_dict(), memory_path)

        if resolved_value is None:
            return ""

        return str(resolved_value)

    return PARTIAL_MEMORY_TEMPLATE_PATTERN.sub(replace_partial, value)


def execute_function_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
) -> ExecutorResult:
    config = node.get("config") or {}

    function_name = config.get("function_name")
    raw_params = config.get("params") or {}

    save_to_memory = config.get("save_to_memory", True)
    save_as = config.get("save_as")
    flatten_result = config.get("flatten_result", False)

    if not function_name:
        return ExecutorResult(
            status="failed",
            message="Function Node에 function_name이 설정되어 있지 않습니다.",
            next_behavior="fail",
            error={
                "code": "FUNCTION_NAME_MISSING",
                "message": "config.function_name is required.",
            },
        )

    registered_function = get_task_function(function_name)

    if not registered_function:
        return ExecutorResult(
            status="failed",
            message=f"등록되지 않은 Function Node입니다: {function_name}",
            next_behavior="fail",
            error={
                "code": "FUNCTION_NOT_ALLOWED",
                "message": f"Function is not registered: {function_name}",
            },
        )

    try:
        resolved_params = _resolve_memory_template_value(
            value=raw_params,
            memory=memory,
        )

        function_result = registered_function.handler(
            resolved_params,
            memory.to_dict(),
        )

        memory_updates: dict[str, Any] = {}

        if save_to_memory:
            if flatten_result:
                if not isinstance(function_result, dict):
                    return ExecutorResult(
                        status="failed",
                        message="flatten_result=true를 사용하려면 함수 결과가 dict여야 합니다.",
                        next_behavior="fail",
                        error={
                            "code": "FUNCTION_RESULT_NOT_DICT",
                            "message": "function_result must be dict when flatten_result is true.",
                        },
                    )

                memory_updates.update(function_result)
            else:
                result_key = save_as or f"{function_name}_result"
                memory_updates[result_key] = function_result

        return ExecutorResult(
            status="success",
            message=None,
            memory_updates=memory_updates,
            next_behavior="evaluate_edges",
        )

    except Exception as error:
        return ExecutorResult(
            status="failed",
            message="Function Node 실행 중 오류가 발생했습니다.",
            next_behavior="fail",
            error={
                "code": "FUNCTION_EXECUTION_FAILED",
                "message": str(error),
                "function_name": function_name,
            },
        )