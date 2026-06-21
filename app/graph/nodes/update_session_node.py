from app.graph.state import AgentState
from app.memory.redis_session import save_session_state


def update_session_node(state: AgentState) -> AgentState:
    """
    Redis 세션에는 대화 메시지 자체가 아니라
    대화 히스토리로 표현되지 않는 구조화된 상태(예: 진행 중인 task)만 저장한다.
    직전 메시지/응답은 Supabase conversation_history로 충분히 커버되므로 중복 저장하지 않는다.
    """
    previous_state = state.get("session_state", {})

    updated_state = {
        **previous_state,
        "last_intent": state.get("intent"),
    }

    # 예약 문의면 간단한 task 상태도 저장
    if state.get("intent") == "reservation":
        updated_state["active_task"] = "reservation"
        updated_state["step"] = previous_state.get("step") or "started"

    state["session_state"] = updated_state

    save_session_state(
        organization_id=state["organization_id"],
        session_id=state["session_id"],
        state=updated_state,
    )

    return state