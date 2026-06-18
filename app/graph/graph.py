from langgraph.graph import StateGraph, END

from app.graph.state import AgentState
from app.graph.nodes.load_session_node import load_session_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.router_node import router_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.rule_node import rule_node
from app.graph.nodes.response_node import response_node
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.update_session_node import update_session_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node


def build_graph():
    """
    Front Agent의 기본 LangGraph 흐름을 만든다.

    흐름:
    1. Redis 세션 로드
    2. 상담방 생성/조회 + 고객 메시지 저장
    3. intent 분류
    4. knowledge 검색
    5. rules 조회
    6. AI 응답 생성
    7. AI 메시지 저장
    8. Redis 세션 업데이트
    9. Agent Run Log 저장

    이번 rules 구조에서는 rule_node가 block, warn, handoff 같은 액션을 실행하지 않는다.
    rule_node는 AI가 답변 전에 참고할 응답 규칙 목록만 가져온다.
    """

    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("load_session", load_session_node)
    graph.add_node("conversation", conversation_node)
    graph.add_node("router", router_node)
    graph.add_node("knowledge", knowledge_node)
    graph.add_node("rule", rule_node)
    graph.add_node("response", response_node)
    graph.add_node("save_ai_message", save_ai_message_node)
    graph.add_node("update_session", update_session_node)
    graph.add_node("save_agent_run", save_agent_run_node)

    # 시작점
    graph.set_entry_point("load_session")

    # 실행 순서 연결
    graph.add_edge("load_session", "conversation")
    graph.add_edge("conversation", "router")
    graph.add_edge("router", "knowledge")
    graph.add_edge("knowledge", "rule")
    graph.add_edge("rule", "response")
    graph.add_edge("response", "save_ai_message")
    graph.add_edge("save_ai_message", "update_session")
    graph.add_edge("update_session", "save_agent_run")
    graph.add_edge("save_agent_run", END)

    # 수정 전
    # graph.add_edge("load_session", "conversation")
    # graph.add_edge("conversation", "router")
    # graph.add_edge("router", "rule")
    # graph.add_edge("rule", "knowledge")
    # graph.add_edge("knowledge", "response")
    # graph.add_edge("response", "save_ai_message")
    # graph.add_edge("save_ai_message", "update_session")
    # graph.add_edge("update_session", "save_agent_run")
    # graph.add_edge("save_agent_run", END)

    return graph.compile()


agent_graph = build_graph()