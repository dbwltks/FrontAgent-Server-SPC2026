from dataclasses import asdict, is_dataclass

from app.tasks.repository import TaskRepository
from app.tasks.runner import DynamicTaskRunner


async def execute_run_task(
    *,
    organization_id: str,
    session_id: str,
    user_message: str,
    task_type: str,
    on_delta=None,
    on_trace=None,
    flow_id: str | None = None,
    initial_variables: dict | None = None,
) -> dict:
    """
    DynamicTaskRunner를 실행하고 결과를 표준 dict로 반환.

    on_delta: 최종 응답 텍스트 스트리밍 콜백 (agent_node writer, realtime emit 등)
    on_trace: 태스크 단계 추적 콜백
    """
    repository = TaskRepository()
    runner = DynamicTaskRunner(repository=repository)

    active_session = repository.find_active_session(
        organization_id=organization_id,
        session_id=session_id,
    )

    resolved_flow_id = flow_id
    if active_session is None and resolved_flow_id is None:
        flow = repository.find_enabled_flow_for_task_type(
            organization_id=organization_id,
            task_type=task_type,
        )
        if not flow:
            return {
                "status": "failed",
                "error": f"task_type에 맞는 활성 태스크가 없습니다: {task_type}",
                "answer": "죄송합니다, 해당 서비스를 처리할 수 없습니다.",
            }
        resolved_flow_id = flow["id"]

    task_response = await runner.run(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        flow_id=resolved_flow_id if active_session is None else None,
        initial_variables=initial_variables if active_session is None else None,
        on_trace=on_trace,
    )

    if is_dataclass(task_response):
        task_result = asdict(task_response)
    elif hasattr(task_response, "model_dump"):
        task_result = task_response.model_dump()
    elif isinstance(task_response, dict):
        task_result = task_response
    else:
        task_result = {"status": "unknown"}

    from app.tasks.service_selection import build_service_selection_message
    from app.graph.task_context import slim_task_result

    direct_message = (
        build_service_selection_message(
            variables=task_result.get("variables") or {},
            current_node_key=task_result.get("current_node_key"),
            status=task_result.get("status"),
        )
        or (task_result.get("message") or "").strip()
        or "요청하신 내용을 처리하지 못했습니다. 다시 한 번 말씀해 주시겠어요?"
    )

    if on_delta:
        on_delta(direct_message)

    task_status = task_result.get("status")
    still_active = task_status == "waiting_user_input"
    slim = slim_task_result(task_result) or {}
    if still_active:
        slim["message"] = direct_message

    return {
        "answer": direct_message,
        # agent_node state update 필드
        "intent": "reservation",
        "next_action": "run_task",
        "task_type": task_type,
        "use_knowledge": False,
        "should_end_session": False,
        "active_task": "reservation" if still_active else None,
        "task_step": task_result.get("current_node_key") if still_active else None,
        "task_result": slim if still_active else slim_task_result(task_result),
        "task_status": task_status,
        "final_response": direct_message,
        "messages": [{"role": "assistant", "content": direct_message}],
    }
