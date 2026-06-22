from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def history_from_state_messages(
    messages: list[BaseMessage], exclude_current_turn: bool = False
) -> list[dict]:
    """
    checkpointer가 복원한 LangChain 메시지 목록을 generate_text/stream_text가
    받는 {"role", "content"} 딕셔너리 목록으로 변환한다.

    conversation_node가 이번 턴 사용자 메시지를 state["messages"]에 추가하기 전
    (decision_node, conversation/decision 병렬 실행 구조)에는 messages가 이미
    "직전까지의" 히스토리이므로 exclude_current_turn=False로 그대로 쓴다.
    conversation_node 실행 후(response_node)에는 마지막 항목이 이번 턴 사용자
    메시지이므로 exclude_current_turn=True로 제외한다.
    """
    history_messages = messages[:-1] if exclude_current_turn and messages else messages

    history: list[dict] = []

    for message in history_messages:
        if isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            continue

        content = message.content

        if not content:
            continue

        history.append({"role": role, "content": content})

    return history
