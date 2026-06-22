from app.graph.state import AgentState


def ai_handoff_node(state: AgentState) -> AgentState:
    """
    상담방의 ai_enabled가 꺼져 있을 때 AI 응답 생성을 건너뛰고
    관리자 응답 대기 상태로 표시한다.
    """
    state["intent"] = "handoff"
    state["next_action"] = "handoff"
    state["task_type"] = "none"
    state["use_knowledge"] = False
    state["decision_reason"] = "AI 자동응답이 꺼져 있어 관리자 응답 대기 상태로 전환"
    state["task_result"] = None
    state["should_use_knowledge"] = False
    state["final_response"] = None
    state["rules"] = []
    state["rule_instructions"] = ""
    state["applied_rules"] = []
    state["knowledge_context"] = []
    state["used_knowledge"] = []

    return state
