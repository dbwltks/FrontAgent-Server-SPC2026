import json
import logging

from app.graph.state import AgentState
from app.graph.nodes.knowledge_node import fallback_split_knowledge_queries
from app.providers.openai_provider import generate_text


logger = logging.getLogger(__name__)


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

MAX_KNOWLEDGE_QUERIES = 3


DECISION_INSTRUCTIONS = f"""
고객 메시지를 분석해 JSON만 출력한다. 설명·마크다운·코드블록 금지.
직전 대화 맥락이 주어지면, 후속 질문("그거 얼마야?" 등)의 의도를 맥락에 맞게 판단한다.

{{"intent":"pricing|reservation|handoff|faq|general","next_action":"search_knowledge|run_task|handoff|respond_general","task_type":"reservation_create|reservation_lookup|reservation_cancel|reservation_update|none","use_knowledge":true/false,"knowledge_queries":["..."],"reason":"한 줄 이유"}}

규칙:
- 정보 문의(가격·정책·안내·서비스 차이 등) → search_knowledge, use_knowledge:true, task_type:none
- 예약 실행 요청 → run_task, use_knowledge:false (create/lookup/cancel/update 구분)
- 상담사·직원 연결 → handoff, use_knowledge:false
- 인사·잡담 → respond_general, use_knowledge:false
- 애매하면 search_knowledge 선택

knowledge_queries 규칙:
- use_knowledge가 true일 때만 채운다. 그 외에는 빈 배열로 둔다.
- 사용자가 여러 정보를 한 번에 물어보면 서로 다른 정보 문의를 최대 {MAX_KNOWLEDGE_QUERIES}개까지 분리한다.
- 각 항목은 knowledge 검색에 바로 사용할 수 있는 짧고 명확한 한국어 질문으로 만든다.
- 예약/상담사 연결/인사 요청은 절대 knowledge_queries에 넣지 않는다.
- 같은 의미의 질문은 중복 제거한다.
""".strip()


def parse_knowledge_queries(raw_queries, use_knowledge: bool, user_message: str) -> list[str]:
    """
    decision_node가 함께 반환한 knowledge_queries를 정리한다.
    use_knowledge가 false면 검색할 필요가 없으므로 빈 배열을 반환한다.
    형식이 깨졌으면 정규식 기반 fallback으로 최소한의 질문 분해를 시도한다.
    """
    if not use_knowledge:
        return []

    queries: list[str] = []

    if isinstance(raw_queries, list):
        for item in raw_queries:
            if not isinstance(item, str):
                continue

            query = item.strip()

            if len(query) < 2:
                continue

            if query not in queries:
                queries.append(query)

            if len(queries) >= MAX_KNOWLEDGE_QUERIES:
                break

    if queries:
        return queries

    return fallback_split_knowledge_queries(user_message)


def parse_decision_result(raw_result: str, user_message: str) -> dict:
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
            "knowledge_queries": [],
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

    knowledge_queries = parse_knowledge_queries(
        data.get("knowledge_queries"),
        use_knowledge,
        user_message,
    )

    return {
        "intent": intent,
        "next_action": next_action,
        "task_type": task_type,
        "use_knowledge": use_knowledge,
        "knowledge_queries": knowledge_queries,
        "reason": reason,
    }


async def decision_node(state: AgentState) -> AgentState:
    """
    intent 분류용 LLM 호출이 실패(타임아웃, rate limit 등)해도
    전체 응답 생성이 500으로 끊기지 않도록 일반 응답 경로로 fallback한다.
    """
    conversation_history = state.get("conversation_history")

    try:
        raw_result = (await generate_text(
            instructions=DECISION_INSTRUCTIONS,
            user_message=state["user_message"],
            conversation_history=conversation_history or None,
        )).strip()
    except Exception:
        logger.warning("decision_node LLM call failed, falling back to general response", exc_info=True)
        raw_result = ""

    decision = parse_decision_result(raw_result, state["user_message"])

    state["intent"] = decision["intent"]
    state["next_action"] = decision["next_action"]
    state["task_type"] = decision["task_type"]
    state["use_knowledge"] = decision["use_knowledge"]
    state["knowledge_queries"] = decision["knowledge_queries"]
    state["decision_reason"] = decision["reason"]

    # 기존 should_use_knowledge_node와의 호환용
    state["should_use_knowledge"] = decision["use_knowledge"]

    return state