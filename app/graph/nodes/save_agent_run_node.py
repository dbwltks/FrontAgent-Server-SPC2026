from app.graph.state import AgentState
from app.repositories.agent_run_repo import create_agent_run


def save_agent_run_node(state: AgentState) -> AgentState:
    """
    LangGraph 마지막 단계에서 실행된다.

    지금까지 처리된 state 값을 기반으로
    Supabase agent_runs 테이블에 실행 로그를 저장한다.
    """

    # 실행 로그 저장은 best-effort다.
    # 여기서 예외가 발생해도 이미 AI 응답은 생성/저장이 끝난 상태이므로,
    # 로그 저장 실패가 전체 요청 실패(500)로 이어지지 않도록 한다.
    try:
        create_agent_run(
            organization_id=state["organization_id"],
            session_id=state["session_id"],
            user_message=state["user_message"],
            intent=state.get("intent"),
            applied_rules=state.get("applied_rules", []),
            used_knowledge=state.get("used_knowledge", []),
            final_response=state.get("final_response"),
            status="success",
            error_message=None,
        )
    except Exception as e:
        print(f"Failed to save agent run log: {e}")

    return state