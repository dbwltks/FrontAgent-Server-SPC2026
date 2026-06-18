import json

from app.graph.state import AgentState
from app.providers.openai_provider import generate_text


INTENT_LIST = [
    "pricing",
    "reservation",
    "handoff",
    "faq",
    "general",
]

NEXT_ACTION_LIST = [
    "search_knowledge",
    "run_task",
    "handoff",
    "respond_general",
]

TASK_TYPE_LIST = [
    "reservation_create",
    "reservation_lookup",
    "reservation_cancel",
    "reservation_update",
    "none",
]


DECISION_INSTRUCTIONS = f"""
너는 고객 메시지를 보고 서버가 다음에 어떤 처리를 해야 하는지 결정하는 decision node다.

반드시 JSON만 출력한다.
설명 문장, 마크다운, 코드블록은 절대 출력하지 않는다.

출력 JSON 형식:
{{
  "intent": "pricing | reservation | handoff | faq | general",
  "next_action": "search_knowledge | run_task | handoff | respond_general",
  "task_type": "reservation_create | reservation_lookup | reservation_cancel | reservation_update | none",
  "use_knowledge": true 또는 false,
  "reason": "짧은 판단 이유"
}}

판단 기준:

1. 고객이 가격, 비용, 요금, 상품 구성, 서비스 차이, 환불, 위치, 영업시간, 정책, 안내 등
   회사가 등록한 문서에서 답해야 할 가능성이 있으면:
   - next_action: "search_knowledge"
   - use_knowledge: true
   - task_type: "none"

2. 고객이 예약 생성, 예약 가능 시간 조회, 예약 변경, 예약 취소처럼
   실제 예약 데이터나 캘린더 확인이 필요한 요청을 하면:
   - next_action: "run_task"
   - use_knowledge: false
   - task_type은 상황에 맞게 선택한다.
     - 예약하고 싶다, 예약해줘, 예약할래 → reservation_create
     - 예약 가능한 시간, 빈 시간, 일정 확인 → reservation_lookup
     - 예약 취소 → reservation_cancel
     - 예약 변경 → reservation_update

3. 고객이 상담사, 직원, 사람 연결을 요청하면:
   - intent: "handoff"
   - next_action: "handoff"
   - task_type: "none"
   - use_knowledge: false

4. 단순 인사, 감사, 의미 없는 일반 대화는:
   - intent: "general"
   - next_action: "respond_general"
   - task_type: "none"
   - use_knowledge: false

5. 애매하지만 회사 문서에 답이 있을 수 있는 질문은 search_knowledge를 선택한다.
   예:
   - 강아지 데리고 가도 돼요?
   - 주차 가능해요?
   - 프리미엄이랑 고급 차이가 뭐예요?
   - 준비물이 있나요?
   - 환불은 어떻게 되나요?

허용 intent:
{INTENT_LIST}

허용 next_action:
{NEXT_ACTION_LIST}

허용 task_type:
{TASK_TYPE_LIST}
""".strip()


def parse_decision_result(raw_result: str) -> dict:
    """
    LLM이 반환한 JSON 문자열을 안전하게 dict로 변환한다.
    JSON 파싱에 실패하면 일반 응답으로 fallback한다.
    """
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return {
            "intent": "general",
            "next_action": "respond_general",
            "task_type": "none",
            "use_knowledge": False,
            "reason": "decision JSON 파싱 실패로 일반 응답 처리",
        }

    intent = data.get("intent")
    next_action = data.get("next_action")
    task_type = data.get("task_type")
    use_knowledge = data.get("use_knowledge")
    reason = data.get("reason")

    if intent not in INTENT_LIST:
        intent = "general"

    if next_action not in NEXT_ACTION_LIST:
        next_action = "respond_general"

    if task_type not in TASK_TYPE_LIST:
        task_type = "none"

    if not isinstance(use_knowledge, bool):
        use_knowledge = next_action == "search_knowledge"

    if not reason:
        reason = ""

    return {
        "intent": intent,
        "next_action": next_action,
        "task_type": task_type,
        "use_knowledge": use_knowledge,
        "reason": reason,
    }


def decision_node(state: AgentState) -> AgentState:
    """
    고객 메시지를 보고 다음 처리 방향을 결정한다.

    기존 router_node + should_use_knowledge_node 역할을 통합한다.
    나중에 task_node가 생기면 next_action과 task_type을 기준으로 연결하면 된다.
    """
    raw_result = generate_text(
        instructions=DECISION_INSTRUCTIONS,
        user_message=state["user_message"],
    ).strip()

    decision = parse_decision_result(raw_result)

    state["intent"] = decision["intent"]
    state["next_action"] = decision["next_action"]
    state["task_type"] = decision["task_type"]
    state["use_knowledge"] = decision["use_knowledge"]
    state["decision_reason"] = decision["reason"]

    # 기존 should_use_knowledge_node와의 호환용
    state["should_use_knowledge"] = decision["use_knowledge"]

    return state