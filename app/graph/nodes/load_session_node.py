from app.graph.state import AgentState
from app.memory.redis_session import get_session_state


def load_session_node(state: AgentState) -> AgentState:
    session_state = get_session_state(
        organization_id=state["organization_id"],
        session_id=state["session_id"],
    )

    state["session_state"] = session_state
    return state