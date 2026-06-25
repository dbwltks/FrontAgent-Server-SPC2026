from dataclasses import asdict, is_dataclass
from typing import Any

from langgraph.config import get_stream_writer

from app.graph.state import AgentState
from app.tasks.repository import TaskRepository
from app.tasks.runner import DynamicTaskRunner


def _task_response_to_dict(task_response: Any) -> dict:
    if is_dataclass(task_response):
        return asdict(task_response)

    if hasattr(task_response, "model_dump"):
        return task_response.model_dump()

    if hasattr(task_response, "dict"):
        return task_response.dict()

    if isinstance(task_response, dict):
        return task_response

    return {
        "handled": getattr(task_response, "handled", None),
        "message": getattr(task_response, "message", None),
        "status": getattr(task_response, "status", None),
        "flow_id": getattr(task_response, "flow_id", None),
        "task_session_id": getattr(task_response, "task_session_id", None),
        "current_node_key": getattr(task_response, "current_node_key", None),
        "variables": getattr(task_response, "variables", None),
        "error": getattr(task_response, "error", None),
        "trace": getattr(task_response, "trace", None),
    }


async def task_node(state: AgentState) -> dict:
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]

    repository = TaskRepository()
    runner = DynamicTaskRunner(repository=repository)

    active_session = repository.find_active_session(
        organization_id=organization_id,
        session_id=session_id,
    )

    flow_id = None
    writer = get_stream_writer()

    if active_session is None:
        task_type = state.get("task_type")

        if not task_type or task_type == "none":
            task_result = {
                "handled": False,
                "message": None,
                "status": "failed",
                "error": {
                    "code": "TASK_TYPE_MISSING",
                    "message": "실행할 task_type이 없습니다.",
                },
            }

            return {
                "task_result": task_result,
                "task_handled": False,
                "task_status": "failed",
            }

        flow = repository.find_enabled_flow_for_task_type(
            organization_id=organization_id,
            task_type=task_type,
        )

        if not flow:
            task_result = {
                "handled": False,
                "message": None,
                "status": "failed",
                "error": {
                    "code": "TASK_FLOW_NOT_FOUND",
                    "message": f"task_type에 맞는 활성 태스크가 없습니다: {task_type}",
                },
            }

            return {
                "task_result": task_result,
                "task_handled": False,
                "task_status": "failed",
            }

        flow_id = flow["id"]

    task_response = await runner.run(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        flow_id=flow_id,
        on_trace=lambda item: writer(
            {
                "type": "task_step",
                "step": item,
            }
        ),
    )

    task_result = _task_response_to_dict(task_response)

    return {
        "task_result": task_result,
        "task_handled": bool(task_result.get("handled")),
        "task_status": task_result.get("status"),
        "session_state": task_result.get("variables") or {},
    }
