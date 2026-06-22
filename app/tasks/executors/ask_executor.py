from typing import Any

from app.tasks.executors.message_executor import render_memory_template
from app.tasks.memory import TaskMemory
from app.tasks.types import ExecutorResult


def execute_ask_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
) -> ExecutorResult:
    config = node.get("config") or {}

    variable_name = config.get("variable_name")
    question = config.get("question") or node.get("label") or "필요한 정보를 입력해 주세요."

    if is_waiting_input:
        if not variable_name:
            return ExecutorResult(
                status="failed",
                message="Ask Node에 variable_name이 설정되어 있지 않습니다.",
                next_behavior="fail",
                error={
                    "code": "ASK_NODE_VARIABLE_NAME_MISSING",
                    "message": "config.variable_name is required.",
                },
            )

        return ExecutorResult(
            status="success",
            message=None,
            memory_updates={
                variable_name: user_message,
            },
            next_behavior="evaluate_edges",
        )

    return ExecutorResult(
        status="success",
        message=render_memory_template(question, memory),
        next_behavior="wait_user",
    )