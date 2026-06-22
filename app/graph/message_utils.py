from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def history_from_state_messages(messages: list[BaseMessage]) -> list[dict]:
    """
    checkpointer가 복원한 LangChain 메시지 목록을 generate_text/stream_text가
    받는 {"role", "content"} 딕셔너리 목록으로 변환한다.

    state["messages"]의 마지막 항목은 이번 턴에 conversation_node가 막 추가한
    현재 사용자 메시지이므로, "직전까지의" 히스토리에서는 제외한다.
    """
    history_messages = messages[:-1] if messages else []

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
