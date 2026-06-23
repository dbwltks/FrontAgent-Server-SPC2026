import logging
import threading

from app.graph.state import AgentState
from app.repositories.agent_run_repo import create_agent_run


logger = logging.getLogger(__name__)


def save_agent_run_node(state: AgentState) -> AgentState:
    """
    LangGraph 마지막 단계에서 실행된다.

    지금까지 처리된 state 값을 기반으로
    Supabase agent_runs 테이블에 실행 로그를 저장한다.
    """

    payload = {
        "organization_id": state["organization_id"],
        "session_id": state["session_id"],
        "user_message": state["user_message"],
        "intent": state.get("intent"),
        "applied_rules": state.get("applied_rules", []),
        "used_knowledge": state.get("used_knowledge", []),
        "final_response": state.get("final_response"),
        "status": "success",
        "error_message": None,
    }

    def _save_agent_run():
        try:
            create_agent_run(**payload)
        except Exception:
            logger.warning(
                "Failed to save agent run log: organization_id=%s, session_id=%s",
                payload["organization_id"],
                payload["session_id"],
                exc_info=True,
            )

    threading.Thread(target=_save_agent_run, daemon=True).start()

    return state
