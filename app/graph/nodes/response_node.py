from app.graph.state import AgentState
from app.graph.prompt_builder import build_response_instructions
from app.providers.openai_provider import generate_text


def response_node(state: AgentState) -> AgentState:
    """
    AI 최종 응답을 생성하는 노드.

    현재 노드는 전체 응답을 한 번에 생성한다.
    WebSocket streaming에서는 별도 streaming_runner를 사용한다.
    """

    # 1. state에서 응답 생성에 필요한 값 가져오기
    intent = state.get("intent")
    applied_rules = state.get("applied_rules", [])
    knowledge_context = state.get("knowledge_context", [])
    session_state = state.get("session_state")
    user_message = state["user_message"]

    # 2. 규칙, 지식, 이전 대화 맥락을 포함한 AI instructions 생성
    instructions = build_response_instructions(
        intent=intent,
        applied_rules=applied_rules,
        knowledge_context=knowledge_context,
        session_state=session_state,
    )

    # 3. OpenAI 전체 응답 생성
    final_response = generate_text(
        instructions=instructions,
        user_message=user_message,
    )

    # 4. 최종 응답을 state에 저장
    state["final_response"] = final_response

    return state