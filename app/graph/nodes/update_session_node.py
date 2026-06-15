from app.graph.state import AgentState
from app.memory.redis_session import save_session_state


def update_session_node(state: AgentState) -> AgentState:
    previous_state = state.get("session_state", {})

    updated_state = {
        **previous_state,
        "last_intent": state.get("intent"),
        "last_user_message": state.get("user_message"),
        "last_response": state.get("final_response"),
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