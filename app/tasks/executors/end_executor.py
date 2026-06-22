from typing import Any

from app.tasks.memory import TaskMemory
from app.tasks.template_renderer import render_text_template
from app.tasks.types import ExecutorResult


def execute_end_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
) -> ExecutorResult:
    config = node.get("config") or {}
    message = config.get("message") or node.get("label") or "태스크가 완료되었습니다."

    status = config.get("status") or "completed"

    if status == "handoff":
        next_behavior = "handoff"
    elif status == "failed":
        next_behavior = "fail"
    else:
        next_behavior = "complete"

    return ExecutorResult(
        status="success",
        message=render_text_template(message, memory),
        next_behavior=next_behavior,
    )