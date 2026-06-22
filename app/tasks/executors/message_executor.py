import re
from typing import Any

from app.tasks.memory import TaskMemory
from app.tasks.types import ExecutorResult


TEMPLATE_PATTERN = re.compile(r"{{\s*memory\.([a-zA-Z0-9_]+)\s*}}")


def render_memory_template(text: str, memory: TaskMemory) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = memory.get(key, "")
        return str(value) if value is not None else ""

    return TEMPLATE_PATTERN.sub(replace, text)


def execute_message_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
) -> ExecutorResult:
    config = node.get("config") or {}
    message = config.get("message") or node.get("label") or ""

    return ExecutorResult(
        status="success",
        message=render_memory_template(message, memory),
        next_behavior="evaluate_edges",
    )