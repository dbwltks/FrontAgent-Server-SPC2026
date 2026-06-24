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
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node


def route_after_join(state: AgentState) -> str:
    """
    conversation/decision/rule 병렬 브랜치가 끝난 뒤 다음 노드를 결정한다.

    우선순위:
    1. AI 자동응답 꺼짐 → ai_handoff
    2. 진행 중 task_session 있음 → task (conversation_node가 미리 조회한 플래그)
    3. decision_node 결과에 따라 task / knowledge / response
    """
    if not state.get("ai_enabled", True):
        return "ai_handoff"

    if state.get("has_active_task", False):
        return "task"

    return route_after_decision(state)


def route_after_decision(state: AgentState) -> str:
    """
    decision_node가 결정한 next_action에 따라 다음 노드를 선택한다.

    rules는 START에서 병렬로 이미 조회되므로 별도 rule 단계를 거치지 않고
    knowledge 또는 곧바로 response로 분기한다.
    """
    next_action = state.get("next_action")

    if next_action == "run_task":
        return "task"

    if next_action == "search_knowledge":
        return "knowledge"

    return "response"


def join_after_conversation_and_decision(state: AgentState) -> AgentState:
    """
    conversation/decision 두 병렬 브랜치가 합류하는 지점.
    두 노드 모두 state를 직접 채우므로 추가 가공 없이 그대로 통과시킨다.
    """
    return state


def build_graph(checkpointer=None):
    """
    Front Agent의 LangGraph 흐름을 만든다.

    흐름:
    1. 상담방 생성/조회 + 고객 메시지 저장 (messages에도 추가)
       와 decision_node의 intent / next_action / task_type / knowledge_queries 판단을
       병렬로 실행한다 (서로의 결과를 필요로 하지 않음).
    2. join에서 ai_enabled가 꺼져 있으면 ai_handoff로 바로 종료,
       아니면 decision 결과로 분기.
    3. next_action이 search_knowledge면 Knowledge 검색 (질문 분해는 decision_node 결과 재사용)
    4. rules 조회
    5. AI 응답 생성 (messages에도 추가)
    6. AI 메시지 저장
    7. Agent Run Log 저장

    멀티턴 메모리(직전 대화 맥락)는 checkpointer가 thread_id 기준으로
    state["messages"]를 자동 영속화/복원하므로 별도 조회 노드가 필요 없다.
    """

    graph = StateGraph(AgentState)

    # 노드 등록
    graph.add_node("conversation", conversation_node)
    graph.add_node("ai_handoff", ai_handoff_node)
    graph.add_node("decision", decision_node)
    graph.add_node("join", join_after_conversation_and_decision)
    graph.add_node("task", task_node)
    graph.add_node(
        "knowledge",
        knowledge_node,
        retry_policy=RetryPolicy(max_attempts=2),
    )
    graph.add_node("rule", rule_node)
    graph.add_node("response", response_node)
    graph.add_node("save_ai_message", save_ai_message_node)
    graph.add_node("save_agent_run", save_agent_run_node)

    # conversation(DB 조회)·decision(LLM 분류)·rule(DB 조회)을 동시에 시작한다.
    # rule은 org 단위 활성 규칙만 읽어 다른 노드 결과에 의존하지 않으므로,
    # 응답 직전 직렬 단계로 두지 않고 여기서 병렬로 처리해 첫 토큰 지연을 줄인다.
    # 셋 다 superstep 0에서 시작해 깊이가 같으므로 join은 정확히 한 번만 실행된다.
    graph.add_edge(START, "conversation")
    graph.add_edge(START, "decision")
    graph.add_edge(START, "rule")

    graph.add_edge("conversation", "join")
    graph.add_edge("decision", "join")
    graph.add_edge("rule", "join")

    # AI 자동응답이 꺼져 있으면 decision 결과를 버리고 종료, 아니면 decision 결과로 분기.
    graph.add_conditional_edges(
        "join",
        route_after_join,
        {
            "ai_handoff": "ai_handoff",
            "task": "task",
            "knowledge": "knowledge",
            "response": "response",
        },
    )
    graph.add_edge("ai_handoff", END)

    graph.add_edge("task", "save_ai_message")

    # rules는 START에서 이미 병렬 조회됐으므로 knowledge 후 바로 응답으로 간다.
    graph.add_edge("knowledge", "response")

    # 응답 저장 및 로그 저장
    graph.add_edge("response", "save_ai_message")
    graph.add_edge("save_ai_message", "save_agent_run")
    graph.add_edge("save_agent_run", END)

    return graph.compile(checkpointer=checkpointer)
