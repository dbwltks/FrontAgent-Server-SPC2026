from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    create_conversation_message,
    update_conversation_last_message,
)


def save_ai_message_node(state: AgentState) -> AgentState:
    """
    AI 최종 응답을 conversation_messages에 저장한다.

    response_node 이후에 실행되어야 한다.
    """

    organization_id = state["organization_id"]
    conversation_id = state.get("conversation_id")
    final_response = state.get("final_response")

    # conversation_id 또는 final_response가 없으면 저장하지 않는다.
    if not conversation_id or not final_response:
        return state

    # 1. AI 응답 메시지 저장
    saved_message = create_conversation_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        sender_type="ai",
        sender_name="Front Agent",
        message=final_response,
        metadata={
            "intent": state.get("intent"),
            "applied_rules": state.get("applied_rules", []),
            "used_knowledge": state.get("used_knowledge", []),
        },
    )

    if saved_message is None:
        # 메시지 저장에 실패하면 last_message만 갱신해
        # 목록과 실제 메시지 내역이 어긋나는 상황을 만들지 않는다.
        print(
            f"Failed to save AI message: "
            f"organization_id={organization_id}, conversation_id={conversation_id}"
        )
        return state

    # 2. 상담방 목록의 마지막 메시지를 AI 응답으로 업데이트
    update_conversation_last_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        last_message=final_response,
    )

    return state