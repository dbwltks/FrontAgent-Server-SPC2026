import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.graph.state import AgentState
from app.graph.message_utils import history_from_state_messages
from app.graph.nodes.knowledge_node import normalize_knowledge_queries
from app.providers.langchain_provider import generate_structured


logger = logging.getLogger(__name__)


class TaskRouteDecision(BaseModel):
    """
    태스크 진행 중인 사용자 메시지를 어떤 처리 경로로 보낼지 판단한다.

    여기서는 답변을 생성하지 않는다.
    오직 route만 고른다.
    """

    route: Literal[
        "continue_task",
        "search_knowledge",
        "check_availability",
        "handoff",
        "need_clarification",
    ] = Field(...)

    confidence: float = Field(..., ge=0, le=1)
    reason: str = Field(...)

    knowledge_queries: list[str] = Field(default_factory=list)


TASK_ROUTER_INSTRUCTIONS = """
너는 예약 태스크 진행 중인 사용자의 메시지를 처리 경로로 분류하는 라우터다.
답변을 생성하지 말고 route만 선택한다.

현재 사용자는 이미 태스크 진행 중이다.
하지만 모든 메시지를 태스크 입력값으로 보면 안 된다.

route 기준:

1. continue_task
- 사용자가 현재 태스크가 기다리는 값을 제공한 경우
- 예: 서비스 선택, 날짜 입력, 시간 입력, 주소 입력, 이름 입력, 전화번호 입력
- 현재 태스크 단계의 질문에 직접 답한 경우

2. search_knowledge
- 사용자가 서비스, 상품, 가격, 옵션, 차이, 정책, 준비사항, 주의사항 등 정보성 설명을 물어본 경우
- 이 경우 태스크는 종료하지 않는다.
- 지식 검색 후 원래 태스크 단계로 복귀한다.
- 새 지식이나 새 서비스명이 추가되어도 코드 수정 없이 이 route를 선택해야 한다.

3. check_availability
- 사용자가 예약 가능한 시간, 가장 빠른 시간, 특정 날짜/시간 가능 여부를 물어본 경우
- 이 경우 지식 검색이 아니라 예약/캘린더/DB 조회 흐름으로 보내야 한다.

4. handoff
- 사용자가 사람, 상담사, 직원, 관리자 연결을 요청한 경우

5. need_clarification
- 사용자의 의도가 불명확해서 바로 처리 경로를 고르기 어려운 경우

중요 규칙:
- 질문 형태라고 해서 무조건 search_knowledge로 보내지 않는다.
- 현재 태스크가 기다리는 값에 대한 직접 답변이면 continue_task다.
- 예약 가능 시간/가장 빠른 시간/특정 시간 가능 여부 질문은 check_availability다.
- 서비스 설명/상품 설명/가격/정책/옵션/차이 질문은 search_knowledge다.
- 상품명, 서비스명, 업종별 키워드를 코드에 하드코딩하지 않는 것이 목표다.
- 사용자가 정보성 질문을 했다면 knowledge_queries에 검색에 사용할 짧은 질문을 넣는다.
- search_knowledge가 아니면 knowledge_queries는 빈 배열로 둔다.
""".strip()


FALLBACK_TASK_ROUTE = TaskRouteDecision(
    route="continue_task",
    confidence=0.0,
    reason="task_router LLM 호출 실패로 진행 중 태스크를 우선 유지합니다.",
    knowledge_queries=[],
)


def _build_task_context_text(state: AgentState) -> str:
    active_task_session = state.get("active_task_session") or {}
    current_task_node = state.get("current_task_node") or {}
    current_task_node_config = current_task_node.get("config") or {}

    return f"""
[현재 태스크 상태]
has_active_task: {state.get("has_active_task")}
active_task: {state.get("active_task")}
task_step: {state.get("task_step")}

[현재 task_session]
task_session_id: {active_task_session.get("id")}
flow_id: {state.get("current_task_flow_id") or active_task_session.get("flow_id")}
current_node_key: {state.get("current_task_node_key") or active_task_session.get("current_node_key")}
waiting_node_key: {active_task_session.get("waiting_node_key")}
status: {active_task_session.get("status")}
variables: {active_task_session.get("variables")}

[현재 사용자가 답해야 하는 태스크 노드]
node_key: {current_task_node.get("node_key")}
node_type: {state.get("current_task_node_type") or current_task_node.get("node_type")}
label: {current_task_node.get("label")}
pending_prompt: {state.get("pending_task_prompt")}
config: {current_task_node_config}
""".strip()


def _task_route_update(decision: TaskRouteDecision, user_message: str) -> dict:
    route = decision.route

    knowledge_queries = []
    if route == "search_knowledge":
        knowledge_queries = normalize_knowledge_queries(
            user_message=user_message,
            generated_queries=decision.knowledge_queries,
        )

    if route == "continue_task":
        return {
            "task_route": route,
            "task_route_confidence": decision.confidence,
            "task_route_reason": decision.reason,

            # 기존 진행 중인 task_session을 그대로 이어간다.
            "next_action": "run_task",
            "task_type": None,

            "use_knowledge": False,
            "should_use_knowledge": False,
            "knowledge_queries": [],
            "decision_reason": decision.reason,
        }

    if route == "search_knowledge":
        return {
            "task_route": route,
            "task_route_confidence": decision.confidence,
            "task_route_reason": decision.reason,

            # task_session은 종료하지 않고 지식 검색만 잠깐 수행한다.
            "next_action": "search_knowledge",
            "task_type": "none",

            "use_knowledge": True,
            "should_use_knowledge": True,
            "knowledge_queries": knowledge_queries,
            "decision_reason": decision.reason,
        }

    if route == "check_availability":
        return {
            "task_route": route,
            "task_route_confidence": decision.confidence,
            "task_route_reason": decision.reason,

            # 현재는 별도 availability node가 없으므로 task 흐름으로 보낸다.
            # 나중에 reservation_availability flow를 만들면 task_type만 바꾸면 된다.
            "next_action": "run_task",
            "task_type": "reservation_availability",

            "use_knowledge": False,
            "should_use_knowledge": False,
            "knowledge_queries": [],
            "decision_reason": decision.reason,
        }

    if route == "handoff":
        return {
            "task_route": route,
            "task_route_confidence": decision.confidence,
            "task_route_reason": decision.reason,

            "next_action": "handoff",
            "task_type": "none",

            "use_knowledge": False,
            "should_use_knowledge": False,
            "knowledge_queries": [],
            "decision_reason": decision.reason,
        }

    return {
        "task_route": route,
        "task_route_confidence": decision.confidence,
        "task_route_reason": decision.reason,

        # 애매한 경우에는 task를 억지로 진행하지 않는다.
        # response_node에서 다시 물어보게 한다.
        "next_action": "respond_general",
        "task_type": "none",

        "use_knowledge": False,
        "should_use_knowledge": False,
        "knowledge_queries": [],
        "decision_reason": decision.reason,
    }


async def task_router_node(state: AgentState) -> dict:
    """
    진행 중 태스크가 있을 때만 사용하는 경량 LLM router.

    목적:
    - 사용자가 현재 태스크 입력값을 준 것인지
    - 태스크 중간에 지식 질문을 한 것인지
    - 예약 가능 시간 조회를 요청한 것인지
    - 상담사 연결을 요청한 것인지
    판단한다.

    주의:
    - 상품명/서비스명/가격/반려동물 같은 키워드를 코드에 하드코딩하지 않는다.
    - LLM이 현재 task context와 route 설명을 보고 판단한다.
    """
    user_message = state["user_message"]
    organization_id = state["organization_id"]
    conversation_history = history_from_state_messages(state.get("messages", []))

    task_context_text = _build_task_context_text(state)

    instructions = f"""
{TASK_ROUTER_INSTRUCTIONS}

{task_context_text}
""".strip()

    try:
        decision = await generate_structured(
            organization_id=organization_id,
            instructions=instructions,
            user_message=user_message,
            schema=TaskRouteDecision,
            conversation_history=conversation_history or None,
        )
    except Exception:
        logger.warning(
            "task_router_node LLM call failed, falling back to continue_task",
            exc_info=True,
        )
        decision = FALLBACK_TASK_ROUTE

    return _task_route_update(decision, user_message)