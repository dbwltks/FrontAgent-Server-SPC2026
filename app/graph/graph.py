from langgraph.graph import StateGraph, END

from app.graph.state import AgentState
from app.graph.nodes.load_session_node import load_session_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.router_node import router_node
from app.graph.nodes.should_use_knowledge_node import should_use_knowledge_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.rule_node import rule_node
from app.graph.nodes.response_node import response_node
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.update_session_node import update_session_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node


def route_after_should_use_knowledge(state: AgentState) -> str:
    """
    Knowledge 검색이 필요한지에 따라 다음 노드를 결정한다.

    - True  → knowledge_node 실행
    - False → knowledge_node를 건너뛰고 rule_node로 이동
    """
    if state.get("should_use_knowledge", False):
        return "knowledge"

    return "rule"


def build_graph():
    """
    Front Agent 기본 LangGraph 흐름을 만든다.

    최종 흐름:
    1. Redis 세션 로드
    2. 상담방 생성/조회 + 고객 메시지 저장
    3. intent 분류
    4. Knowledge 검색 필요 여부 판단
    5. 필요하면 Knowledge 검색
    6. rules 조회
    7. AI 응답 생성
    8. AI 메시지 저장
    9. Redis 세션 업데이트
    10. Agent Run Log 저장
    """

    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("load_session", load_session_node)
    graph.add_node("conversation", conversation_node)
    graph.add_node("router", router_node)
    graph.add_node("should_use_knowledge", should_use_knowledge_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("rule", rule_node)
    graph.add_node("response", response_node)
    graph.add_node("save_ai_message", save_ai_message_node)
    graph.add_node("update_session", update_session_node)
    graph.add_node("save_agent_run", save_agent_run_node)

    # 시작점
    graph.set_entry_point("load_session")

    # 기본 흐름
    graph.add_edge("load_session", "conversation")
    graph.add_edge("conversation", "router")
    graph.add_edge("router", "should_use_knowledge")

    # Knowledge 필요 여부에 따라 분기
    graph.add_conditional_edges(
        "should_use_knowledge",
        route_after_should_use_knowledge,
        {
            "knowledge": "knowledge",
            "rule": "rule",
        },
    )

    # Knowledge를 탄 경우에도 최종적으로 rule을 조회한 뒤 response로 간다.
    graph.add_edge("knowledge", "rule")
    graph.add_edge("rule", "response")

    # 응답 저장 및 로그 저장
    graph.add_edge("response", "save_ai_message")
    graph.add_edge("save_ai_message", "update_session")
    graph.add_edge("update_session", "save_agent_run")
    graph.add_edge("save_agent_run", END)

    return graph.compile()


agent_graph = build_graph()