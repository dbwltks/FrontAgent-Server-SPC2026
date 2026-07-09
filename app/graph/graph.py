from langgraph.graph import END, START, StateGraph

from app.graph.handlers.prepare_node import prepare_node
from app.graph.state import AgentState


def build_graph(checkpointer=None):
    """
    Front Agent LangGraph.

    prepare_node 한 super-step만 실행한다:
    - conversation + agent 병렬 처리
    - persistence는 prepare 내부에서 백그라운드 스케줄
    - checkpoint는 durability=exit로 그래프 종료 시 1회만 write
    """

    graph = StateGraph(AgentState)

    graph.add_node("prepare", prepare_node)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", END)

    return graph.compile(checkpointer=checkpointer)
