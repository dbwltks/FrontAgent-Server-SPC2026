from dataclasses import dataclass, field
from typing import Any, Literal


NextBehavior = Literal[
    "evaluate_edges",
    "wait_user",
    "complete",
    "fail",
    "handoff",
]


@dataclass
class ExecutorResult:
    status: str = "success"
    message: str | None = None
    memory_updates: dict[str, Any] = field(default_factory=dict)
    next_behavior: NextBehavior = "evaluate_edges"
    error: dict[str, Any] | None = None


@dataclass
class TaskRunResponse:
    handled: bool
    message: str | None = None
    status: str | None = None
    flow_id: str | None = None
    task_session_id: str | None = None
    current_node_key: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


def normalize_task_error(
    error: dict[str, Any] | None,
    *,
    code: str = "TASK_EXECUTION_FAILED",
    message: str = "Task execution failed.",
    node_key: str | None = None,
    node_type: str | None = None,
) -> dict[str, Any]:
    normalized_error = error.copy() if error else {
        "code": code,
        "message": message,
    }

    if "code" not in normalized_error:
        normalized_error["code"] = code

    if "message" not in normalized_error:
        normalized_error["message"] = message

    if node_key:
        normalized_error["node_key"] = node_key

    if node_type:
        normalized_error["node_type"] = node_type

    return normalized_error