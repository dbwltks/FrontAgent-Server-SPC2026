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


DECISION_INSTRUCTIONS = """
고객 메시지를 분석해 JSON만 출력한다. 설명·마크다운·코드블록 금지.

{"intent":"pricing|reservation|handoff|faq|general","next_action":"search_knowledge|run_task|handoff|respond_general","task_type":"reservation_create|reservation_lookup|reservation_cancel|reservation_update|none","use_knowledge":true/false,"reason":"한 줄 이유"}

규칙:
- 정보 문의(가격·정책·안내·서비스 차이 등) → search_knowledge, use_knowledge:true, task_type:none
- 예약 실행 요청 → run_task, use_knowledge:false (create/lookup/cancel/update 구분)
- 상담사·직원 연결 → handoff, use_knowledge:false
- 인사·잡담 → respond_general, use_knowledge:false
- 애매하면 search_knowledge 선택
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


async def decision_node(state: AgentState) -> AgentState:
    raw_result = (await generate_text(
        instructions=DECISION_INSTRUCTIONS,
        user_message=state["user_message"],
    )).strip()

    decision = parse_decision_result(raw_result)

    state["intent"] = decision["intent"]
    state["next_action"] = decision["next_action"]
    state["task_type"] = decision["task_type"]
    state["use_knowledge"] = decision["use_knowledge"]
    state["decision_reason"] = decision["reason"]

    # 기존 should_use_knowledge_node와의 호환용
    state["should_use_knowledge"] = decision["use_knowledge"]

    return state