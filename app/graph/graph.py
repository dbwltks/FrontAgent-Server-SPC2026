from langgraph.graph import END, StateGraph

from app.graph.state import AgentState
from app.graph.nodes.load_session_node import load_session_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.decision_node import decision_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.rule_node import rule_node
from app.graph.nodes.response_node import response_node
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.update_session_node import update_session_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node


def route_after_decision(state: AgentState) -> str:
    """
    decision_node가 결정한 next_action에 따라 다음 노드를 선택한다.

    현재 단계:
    - search_knowledge → knowledge_node 실행
    - run_task → 아직 task_node가 없으므로 rule_node로 이동
    - handoff → 아직 handoff_node가 없으므로 rule_node로 이동
    - respond_general → rule_node로 이동

    나중에 task_node, handoff_node가 생기면 여기에서 연결만 바꾸면 된다.
    """
    next_action = state.get("next_action")

    if next_action == "search_knowledge":
        return "knowledge"

    return "rule"


def build_graph():
    """
    Front Agent의 기본 LangGraph 흐름을 만든다.

    최종 목표 흐름:
    1. Redis 세션 로드
    2. 상담방 생성/조회 + 고객 메시지 저장
    3. decision_node에서 intent / next_action / task_type 판단
    4. next_action이 search_knowledge면 Knowledge 검색
    5. rules 조회
    6. AI 응답 생성
    7. AI 메시지 저장
    8. Redis 세션 업데이트
    9. Agent Run Log 저장
    """

    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("load_session", load_session_node)
    graph.add_node("conversation", conversation_node)
    graph.add_node("decision", decision_node)
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
    graph.add_edge("conversation", "decision")

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
    graph.add_edge("save_ai_message", "update_session")
    graph.add_edge("update_session", "save_agent_run")
    graph.add_edge("save_agent_run", END)

    return graph.compile()


agent_graph = build_graph()