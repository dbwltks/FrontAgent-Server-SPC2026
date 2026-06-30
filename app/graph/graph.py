from langgraph.graph import END, START, StateGraph

from app.graph.nodes.ai_handoff_node import ai_handoff_node
from app.graph.nodes.finalize_node import finalize_node
from app.graph.nodes.prepare_node import prepare_node
from app.graph.state import AgentState


def route_after_prepare(state: AgentState) -> str:
    """
    prepare_node(conversation + Main LLM agent_node) 실행 후 다음 노드를 고른다.

    - ai_enabled가 꺼져 있으면 ai_handoff로 보낸다(이 경우 agent_node 결과는
      prepare_node가 이미 버렸다).
    - 그 외에는 항상 finalize로 간다 - agent_node가 의도 판단, 지식검색/예약
      tool 호출, 최종 답변 생성, 작별 인사 여부까지 한 번의 호출(+필요시 tool
      1회)로 전부 끝내고 final_response를 채워서 돌아오기 때문이다.
    """
    if not state.get("ai_enabled", True):
        return "ai_handoff"

    return "finalize"


def build_graph(checkpointer=None):
    """
    Front Agent의 LangGraph 흐름을 만든다.

    1. prepare_node가 conversation_node(상담방 조회/생성, 메시지 저장)와
       agent_node(Main LLM + tool calling)를 동시에 실행한다. agent_node는
       OpenAI native function calling으로 search_knowledge/run_task/
       request_handoff 중 필요한 tool을 직접 판단해 호출하므로, 예전처럼
       intent 분류 노드 -> knowledge 노드 -> task 노드 -> response 노드로
       이어지는 직렬 그래프가 필요 없다(노드 전환마다 발생하는 checkpoint
       write 비용과 노드 간 불필요한 LLM 재호출을 없앤다).
    2. ai_enabled가 꺼진 상담방만 ai_handoff로 분기한다.
    3. finalize_node가 AI 메시지 저장, 세션 종료 처리, agent run log 저장을
       한 번에 처리한다.
    """

    graph = StateGraph(AgentState)

    graph.add_node("prepare", prepare_node)
    graph.add_node("ai_handoff", ai_handoff_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "prepare")

    graph.add_conditional_edges(
        "prepare",
        route_after_prepare,
        {
            "ai_handoff": "ai_handoff",
            "finalize": "finalize",
        },
    )

    graph.add_edge("ai_handoff", END)
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
