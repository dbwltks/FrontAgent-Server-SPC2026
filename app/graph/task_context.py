"""
Task session context helpers.

원칙:
- task memory(slots)의 source of truth = Redis/DB task_sessions.variables
- LangGraph checkpointer task_result = 메타(status, node, message, trace)만 유지
"""

from typing import Any

from app.tasks.repository import TaskRepository


_TASK_RESULT_META_KEYS = (
    "handled",
    "message",
    "status",
    "flow_id",
    "task_session_id",
    "current_node_key",
    "error",
    "trace",
)


def load_active_task_session(organization_id: str, session_id: str) -> dict[str, Any] | None:
    return TaskRepository().find_active_session(
        organization_id=organization_id,
        session_id=session_id,
    )


def resolve_task_variables(organization_id: str, session_id: str) -> dict[str, Any]:
    session = load_active_task_session(organization_id, session_id)
    if not session:
        return {}
    return session.get("variables") or {}


def has_active_task_session(
    organization_id: str,
    session_id: str,
    *,
    active_task: str | None = None,
) -> bool:
    if active_task:
        return True
    return load_active_task_session(organization_id, session_id) is not None


def slim_task_result(task_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """checkpointer에 variables 복사본을 남기지 않는다."""
    if not task_result:
        return None
    return {
        key: task_result[key]
        for key in _TASK_RESULT_META_KEYS
        if key in task_result
    }


def build_task_result_meta(
    *,
    task_result: dict[str, Any] | None = None,
    organization_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """
    checkpointer/F FAQ resume용 task_result 메타.
    variables는 포함하지 않는다.
    """
    slim = slim_task_result(task_result) or {}

    if organization_id and session_id:
        session = load_active_task_session(organization_id, session_id)
        if session:
            slim.setdefault("status", session.get("status"))
            slim.setdefault("current_node_key", session.get("current_node_key"))
            slim.setdefault("task_session_id", session.get("id"))
            slim.setdefault("flow_id", session.get("flow_id"))

    return slim or None


def hydrate_task_result_for_response(
    task_result: dict[str, Any] | None,
    organization_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """API 응답용: variables는 Redis/DB에서만 붙인다."""
    if not task_result:
        return task_result

    variables = resolve_task_variables(organization_id, session_id)
    if not variables:
        return task_result

    hydrated = dict(task_result)
    hydrated["variables"] = variables
    return hydrated


def resolve_active_task_step(
    organization_id: str,
    session_id: str,
    *,
    task_step: str | None = None,
) -> str | None:
    session = load_active_task_session(organization_id, session_id)
    if session:
        return session.get("current_node_key") or task_step
    return task_step
