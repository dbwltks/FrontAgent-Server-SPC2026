import logging
from typing import Literal

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from app.graph.state import AgentState
from app.graph.message_utils import history_from_state_messages
from app.graph.nodes.knowledge_node import fallback_split_knowledge_queries
from app.providers.langchain_provider import generate_structured


logger = logging.getLogger(__name__)


MAX_KNOWLEDGE_QUERIES = 3


class DecisionResult(BaseModel):
    """
    decision_node가 OpenAI native structured output으로 직접 받는 스키마.
    모델이 이 스키마를 어길 수 없으므로 JSON 파싱 실패 자체가 일어나지 않는다.
    """

    intent: Literal["pricing", "reservation", "handoff", "faq", "general"]
    next_action: Literal["search_knowledge", "run_task", "handoff", "respond_general"]
    task_type: Literal[
        "reservation_create",
        "reservation_lookup",
        "reservation_cancel",
        "reservation_update",
        "none",
    ]
    use_knowledge: bool
    knowledge_queries: list[str] = Field(default_factory=list)
    reason: str = ""


DECISION_INSTRUCTIONS = f"""
고객 메시지를 분석해 intent/next_action/task_type/use_knowledge/knowledge_queries/reason을 판단한다.
직전 대화 맥락이 주어지면, 후속 질문("그거 얼마야?" 등)의 의도를 맥락에 맞게 판단한다.

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

reason에는 그렇게 판단한 이유를 한 줄로 적는다.
""".strip()


def _normalize_knowledge_queries(
    raw_queries: list[str], use_knowledge: bool, user_message: str
) -> list[str]:
    if not use_knowledge:
        return []

    queries: list[str] = []

    for item in raw_queries:
        query = item.strip()

        if len(query) < 2:
            continue

        if query not in queries:
            queries.append(query)

        if len(queries) >= MAX_KNOWLEDGE_QUERIES:
            break

    if queries:
        return queries

    # 모델이 use_knowledge=true인데 knowledge_queries를 비워서 줄 수 있으므로
    # 정규식 기반 fallback으로 최소한의 질문 분해를 시도한다.
    return fallback_split_knowledge_queries(user_message)


def make_decision_postprocessor(user_message: str) -> RunnableLambda:
    """
    structured output으로 받은 DecisionResult를 체인 안에서 바로 정규화한다.
    knowledge_queries 보강 외에는 스키마(Literal/bool)가 이미 형식을 보장하므로
    추가 검증이 필요 없다.
    """

    def _postprocess(decision: DecisionResult) -> DecisionResult:
        decision.knowledge_queries = _normalize_knowledge_queries(
            decision.knowledge_queries,
            decision.use_knowledge,
            user_message,
        )
        return decision

    return RunnableLambda(_postprocess)


FALLBACK_DECISION = DecisionResult(
    intent="general",
    next_action="respond_general",
    task_type="none",
    use_knowledge=False,
    knowledge_queries=[],
    reason="decision LLM 호출 실패로 일반 응답 처리",
)


async def decision_node(state: AgentState) -> AgentState:
    """
    intent 분류용 LLM 호출이 실패(타임아웃, rate limit 등)해도
    전체 응답 생성이 500으로 끊기지 않도록 일반 응답 경로로 fallback한다.
    """
    conversation_history = history_from_state_messages(state.get("messages", []))
    user_message = state["user_message"]
    organization_id = state["organization_id"]

    try:
        decision = await generate_structured(
            organization_id=organization_id,
            instructions=DECISION_INSTRUCTIONS,
            user_message=user_message,
            schema=DecisionResult,
            conversation_history=conversation_history or None,
            postprocess=make_decision_postprocessor(user_message),
        )
    except Exception:
        logger.warning("decision_node LLM call failed, falling back to general response", exc_info=True)
        decision = FALLBACK_DECISION

    state["intent"] = decision.intent
    state["next_action"] = decision.next_action
    state["task_type"] = decision.task_type
    state["use_knowledge"] = decision.use_knowledge
    state["knowledge_queries"] = decision.knowledge_queries
    state["decision_reason"] = decision.reason

    # 기존 should_use_knowledge_node와의 호환용
    state["should_use_knowledge"] = decision.use_knowledge

    # 예약 진행 상태는 messages 히스토리로 표현되지 않으므로 checkpointer가
    # 영속화하는 구조화된 필드(active_task/task_step)에 별도로 유지한다.
    if decision.intent == "reservation":
        state["active_task"] = "reservation"
        state["task_step"] = state.get("task_step") or "started"

    return state
