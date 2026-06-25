from typing import Any

from app.tasks.memory import TaskMemory
from app.tasks.template_renderer import render_text_template
from app.tasks.types import ExecutorResult


def execute_message_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
    organization_id: str | None = None,
) -> ExecutorResult:
    config = node.get("config") or {}
    message = config.get("message") or node.get("label") or ""

    return ExecutorResult(
        status="success",
        message=render_text_template(message, memory),
        next_behavior="evaluate_edges",
    )