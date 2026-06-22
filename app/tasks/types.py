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