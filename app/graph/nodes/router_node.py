from app.graph.state import AgentState
from app.providers.openai_provider import generate_text


INTENT_LIST = ["pricing", "reservation", "handoff", "faq", "general"]

ROUTER_INSTRUCTIONS = f"""
너는 고객 메시지를 읽고 intent를 분류하는 분류기다.

아래 intent 중 하나만 소문자로 출력한다. 다른 말은 절대 하지 않는다.

- pricing: 가격, 비용, 요금, 금액 관련 문의
- reservation: 예약, 일정, 날짜, 시간 관련 문의
- handoff: 상담원/직원 연결 요청
- faq: 영업시간, 위치, 주소, 일반 안내 문의
- general: 위 어디에도 해당하지 않는 일반 대화

반드시 {INTENT_LIST} 중 하나만 출력한다.
""".strip()


def router_node(state: AgentState) -> AgentState:
    result = generate_text(
        instructions=ROUTER_INSTRUCTIONS,
        user_message=state["user_message"],
    ).strip().lower()

    intent = result if result in INTENT_LIST else "general"

    state["intent"] = intent
    return state
