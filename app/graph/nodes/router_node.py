from app.graph.state import AgentState


def router_node(state: AgentState) -> AgentState:
    message = state["user_message"]

    # 가격 문의를 먼저 판단해야 함
    if any(word in message for word in ["가격", "비용", "얼마", "요금", "금액"]):
        intent = "pricing"

    elif any(word in message for word in ["예약", "일정", "가능한 시간", "예약 가능", "예약하고"]):
        intent = "reservation"

    elif any(word in message for word in ["상담원", "직원", "사람", "연결"]):
        intent = "handoff"

    elif any(word in message for word in ["영업시간", "위치", "주소", "어디"]):
        intent = "faq"

    else:
        intent = "general"

    state["intent"] = intent
    return state