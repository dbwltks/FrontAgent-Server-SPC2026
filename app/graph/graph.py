from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.graph.nodes.task_node import task_node
from app.graph.state import AgentState
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.ai_handoff_node import ai_handoff_node
from app.graph.nodes.decision_node import decision_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.rule_node import rule_node
from app.graph.nodes.response_node import response_node
from app.graph.nodes.end_session_node import end_session_node
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node
from app.graph.nodes.task_router_node import task_router_node


def route_after_conversation(state: AgentState) -> str:
    """
    conversation_node 실행 후,
    진행 중 태스크가 있으면 task_router_node로 보내고
    없으면 기존 decision_node로 보낸다.
    """
    if not state.get("ai_enabled", True):
        return "ai_handoff"

    if state.get("has_active_task", False):
        return "task_router"

    return "decision"


def route_after_decision(state: AgentState) -> str:
    """
    decision_node 또는 task_router_node가 결정한 next_action에 따라 다음 노드를 선택한다.
    """
    next_action = state.get("next_action")

    if next_action == "run_task":
        return "task"

    if next_action == "search_knowledge":
        return "knowledge"

    if next_action == "handoff":
        return "ai_handoff"

    if next_action == "check_availability":
        return "response"

    if next_action == "end_session":
        return "response"

    return "response"


def build_graph(checkpointer=None):
    """
    Front Agent의 LangGraph 흐름을 만든다.

    변경된 흐름:
    1. conversation_node가 상담방 생성/조회, 고객 메시지 저장, active task context 조회를 수행한다.
    2. 진행 중 태스크가 있으면 task_router_node로 간다.
    3. 진행 중 태스크가 없으면 기존 decision_node로 간다.
    4. rule_node는 decision/task_router 이후에 실행한다.
       - 병렬 join을 제거해서 intent, next_action 같은 key의 concurrent update를 막는다.
    5. next_action에 따라 task / knowledge / handoff / response로 분기한다.
    6. response 이후 AI 메시지 저장, 세션 종료 처리, agent run log를 저장한다.
    """

    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("conversation", conversation_node)
    graph.add_node("ai_handoff", ai_handoff_node)
    graph.add_node("decision", decision_node)
    graph.add_node("task_router", task_router_node)
    graph.add_node("task", task_node)
    graph.add_node(
        "knowledge",
        knowledge_node,
        retry_policy=RetryPolicy(max_attempts=2),
    )
    graph.add_node("rule", rule_node)
    graph.add_node("response", response_node)
    graph.add_node("save_ai_message", save_ai_message_node)
    graph.add_node("end_session", end_session_node)
    graph.add_node("save_agent_run", save_agent_run_node)

    # 1. 먼저 conversation_node에서 상담방 정보와 active task context를 조회한다.
    graph.add_edge(START, "conversation")

    # 2. active task 여부에 따라 일반 decision 또는 task 전용 router로 분기한다.
    graph.add_conditional_edges(
        "conversation",
        route_after_conversation,
        {
            "ai_handoff": "ai_handoff",
            "task_router": "task_router",
            "decision": "decision",
        },
    )

    # 3. decision/task_router 이후 rule을 조회한다.
    # 기존 병렬 join 구조는 intent 중복 업데이트 오류를 만들 수 있으므로 제거한다.
    graph.add_edge("decision", "rule")
    graph.add_edge("task_router", "rule")

    # 4. rule 조회 후 next_action에 따라 실행 노드로 분기한다.
    graph.add_conditional_edges(
        "rule",
        route_after_decision,
        {
            "ai_handoff": "ai_handoff",
            "task": "task",
            "knowledge": "knowledge",
            "response": "response",
        },
    )

    graph.add_edge("ai_handoff", END)

    # task 실행 결과도 항상 response를 거쳐 자연스러운 응답을 만든다.
    graph.add_edge("task", "response")

    # knowledge 검색 후 response 생성
    graph.add_edge("knowledge", "response")

    # 응답 저장 및 로그 저장
    graph.add_edge("response", "save_ai_message")
    graph.add_edge("save_ai_message", "end_session")
    graph.add_edge("end_session", "save_agent_run")
    graph.add_edge("save_agent_run", END)

    return graph.compile(checkpointer=checkpointer)