from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from app.graph.state import AgentState
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.ai_handoff_node import ai_handoff_node
from app.graph.nodes.decision_node import decision_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.rule_node import rule_node
from app.graph.nodes.response_node import response_node
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node


def route_after_conversation(state: AgentState) -> str:
    """
    상담방의 AI 자동응답이 꺼져 있으면 decision/knowledge/response를 모두 건너뛰고
    관리자 응답 대기 상태로 바로 종료한다.
    """
    if not state.get("ai_enabled", True):
        return "ai_handoff"

    return "decision"


def route_after_decision(state: AgentState) -> str:
    """
    decision_node가 결정한 next_action에 따라 다음 노드를 선택한다.

    현재 단계:
    - search_knowledge → knowledge_node 실행
    - run_task → 아직 task_node가 없으므로 rule_node로 이동
    - handoff → 아직 handoff_node가 없으므로 rule_node로 이동
    - respond_general → rule_node로 이동

    나중에 task_node, handoff_node가 생기면 여기에서 연결만 바꾸면 된다.

    참고: rule_node를 decision_node와 병렬 실행하는 구조도 검토했으나,
    LangGraph는 fan-in되는 두 경로의 깊이(super-step 수)가 정확히 같아야
    join 노드가 한 번만 실행된다. knowledge 분기(decision→knowledge, 깊이 2)와
    rule 분기(깊이 1)의 깊이가 달라 응답 생성이 두 번 실행되는 문제가 확인되어
    순차 구조를 유지한다.
    """
    next_action = state.get("next_action")

    if next_action == "search_knowledge":
        return "knowledge"

    return "rule"


def build_graph(checkpointer=None):
    """
    Front Agent의 LangGraph 흐름을 만든다.

    흐름:
    1. 상담방 생성/조회 + 고객 메시지 저장 (messages에도 추가)
    2. ai_enabled가 꺼져 있으면 ai_handoff로 바로 종료
    3. decision_node에서 intent / next_action / task_type / knowledge_queries 판단
    4. next_action이 search_knowledge면 Knowledge 검색 (질문 분해는 decision_node 결과 재사용)
    5. rules 조회
    6. AI 응답 생성 (messages에도 추가)
    7. AI 메시지 저장
    8. Agent Run Log 저장

    멀티턴 메모리(직전 대화 맥락)는 checkpointer가 thread_id 기준으로
    state["messages"]를 자동 영속화/복원하므로 별도 조회 노드가 필요 없다.
    """

    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("conversation", conversation_node)
    graph.add_node("ai_handoff", ai_handoff_node)
    graph.add_node("decision", decision_node)
    graph.add_node(
        "knowledge",
        knowledge_node,
        retry_policy=RetryPolicy(max_attempts=2),
    )
    graph.add_node("rule", rule_node)
    graph.add_node("response", response_node)
    graph.add_node("save_ai_message", save_ai_message_node)
    graph.add_node("save_agent_run", save_agent_run_node)

    # 시작점
    graph.set_entry_point("conversation")

    # AI 자동응답이 꺼져 있으면 decision 단계로 가지 않고 바로 종료한다.
    graph.add_conditional_edges(
        "conversation",
        route_after_conversation,
        {
            "ai_handoff": "ai_handoff",
            "decision": "decision",
        },
    )
    graph.add_edge("ai_handoff", END)

    # decision 결과에 따라 Knowledge 검색 여부 결정
    graph.add_conditional_edges(
        "decision",
        route_after_decision,
        {
            "knowledge": "knowledge",
            "rule": "rule",
        },
    )

    # Knowledge를 탄 경우에도 최종적으로 rule을 조회한다.
    graph.add_edge("knowledge", "rule")

    # rule은 최종 응답 생성 직전에 조회한다.
    graph.add_edge("rule", "response")

    # 응답 저장 및 로그 저장
    graph.add_edge("response", "save_ai_message")
    graph.add_edge("save_ai_message", "save_agent_run")
    graph.add_edge("save_agent_run", END)

    return graph.compile(checkpointer=checkpointer)
