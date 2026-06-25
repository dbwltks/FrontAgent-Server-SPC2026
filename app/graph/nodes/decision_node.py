import logging
from typing import Literal

from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from app.graph.state import AgentState
from app.graph.message_utils import history_from_state_messages
from app.graph.nodes.knowledge_node import (
    fallback_split_knowledge_queries,
    normalize_knowledge_queries,
)
from app.providers.langchain_provider import generate_structured
from app.tasks.repository import TaskRepository


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


DECISION_INSTRUCTIONS = f"""
고객 메시지를 분석해 intent/next_action/task_type/use_knowledge/knowledge_queries를 판단한다.
직전 대화 맥락이 주어지면, 후속 질문("그거 얼마야?" 등)의 의도를 맥락에 맞게 판단한다.

규칙:
- 정보 문의(가격·정책·안내·서비스 차이 등) → search_knowledge, use_knowledge:true, task_type:none
- 예약 실행 요청 → run_task, use_knowledge:false (create/lookup/cancel/update 구분)
- 상담사·직원 연결 → handoff, use_knowledge:false
- 인사·잡담 → respond_general, use_knowledge:false
- 애매하면 search_knowledge 선택

knowledge_queries 규칙:
- use_knowledge가 true일 때만 채운다. 그 외에는 빈 배열로 둔다.
- 단순 질문은 검색어 1개만 만든다.
- 사용자가 여러 정보를 한 번에 물어보는 복합 질문일 때만 서로 다른 정보 문의로 분리한다.
- 원문 질문을 포함한 최종 검색어는 최대 {MAX_KNOWLEDGE_QUERIES}개다.
- 각 항목은 knowledge 검색에 바로 사용할 수 있는 짧고 명확한 한국어 질문으로 만든다.
- 예약/상담사 연결/인사 요청은 절대 knowledge_queries에 넣지 않는다.
- 같은 의미의 질문은 중복 제거한다.
""".strip()


def _normalize_knowledge_queries(
    raw_queries: list[str], use_knowledge: bool, user_message: str
) -> list[str]:
    if not use_knowledge:
        return []

    generated_queries = raw_queries or fallback_split_knowledge_queries(user_message)
    return normalize_knowledge_queries(user_message, generated_queries)


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
)

ACTIVE_TASK_DECISION = DecisionResult(
    intent="reservation",
    next_action="run_task",
    task_type="none",
    use_knowledge=False,
    knowledge_queries=[],
)


def _has_active_task_session(organization_id: str, session_id: str) -> bool:
    try:
        return (
            TaskRepository().find_active_session(
                organization_id=organization_id,
                session_id=session_id,
            )
            is not None
        )
    except Exception:
        return False


async def decision_node(state: AgentState) -> dict:
    """
    intent 분류용 LLM 호출이 실패(타임아웃, rate limit 등)해도
    전체 응답 생성이 500으로 끊기지 않도록 일반 응답 경로로 fallback한다.

    conversation_node와 병렬(같은 superstep)로 실행되므로, 자신이 바꾸지 않는
    키(organization_id 등)는 절대 포함하지 않고 변경분만 dict로 반환해야 한다.
    그렇지 않으면 두 노드가 같은 키에 동시에 값을 쓰는 것으로 인식되어
    LangGraph가 InvalidUpdateError를 낸다.
    """
    conversation_history = history_from_state_messages(state.get("messages", []))
    user_message = state["user_message"]
    organization_id = state["organization_id"]
    session_id = state["session_id"]

    # 진행 중인 task_session이 있으면 join 라우팅(route_after_join)이
    # 이 노드의 결과를 무시하고 무조건 task로 보낸다. 그 경우 LLM 호출은
    # 결과가 버려지는 헛수고이므로, 빠른 DB 체크로 건너뛴다.
    if _has_active_task_session(organization_id, session_id):
        decision = ACTIVE_TASK_DECISION
        update: dict = {
            "intent": decision.intent,
            "next_action": decision.next_action,
            "task_type": decision.task_type,
            "use_knowledge": decision.use_knowledge,
            "knowledge_queries": decision.knowledge_queries,
            "should_use_knowledge": decision.use_knowledge,
            "active_task": "reservation",
            "task_step": state.get("task_step") or "started",
        }
        return update

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

    update: dict = {
        "intent": decision.intent,
        "next_action": decision.next_action,
        "task_type": decision.task_type,
        "use_knowledge": decision.use_knowledge,
        "knowledge_queries": decision.knowledge_queries,
        # 기존 should_use_knowledge_node와의 호환용
        "should_use_knowledge": decision.use_knowledge,
    }

    # 예약 진행 상태는 messages 히스토리로 표현되지 않으므로 checkpointer가
    # 영속화하는 구조화된 필드(active_task/task_step)에 별도로 유지한다.
    if decision.intent == "reservation":
        update["active_task"] = "reservation"
        update["task_step"] = state.get("task_step") or "started"

    return update
