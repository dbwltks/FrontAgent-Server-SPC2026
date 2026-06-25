from typing import Any

from app.tasks.function_registry import get_task_function
from app.tasks.memory import TaskMemory
from app.tasks.template_renderer import resolve_template_value
from app.tasks.types import ExecutorResult


def execute_function_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
    organization_id: str | None = None,
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
        resolved_params = resolve_template_value(
            value=raw_params,
            memory=memory,
        )

        # config.params에 organization_id를 명시하지 않은 함수 노드는
        # 현재 세션의 organization_id를 자동으로 채워준다.
        if organization_id and "organization_id" not in resolved_params:
            resolved_params = {**resolved_params, "organization_id": organization_id}

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