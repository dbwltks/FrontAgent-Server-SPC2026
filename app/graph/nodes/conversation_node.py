from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    get_or_create_conversation,
    create_conversation_message,
    update_conversation_last_message,
)


def conversation_node(state: AgentState) -> AgentState:
    """
    상담방을 찾거나 생성하고,
    사용자의 메시지를 conversation_messages에 저장한다.

    추가 역할:
    - conversation.ai_enabled 값을 state에 저장한다.
    - 이후 WebSocket runner가 AI 응답 생성 여부를 판단할 수 있다.
    """

    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]

    # 1. organization_id + session_id 기준으로 상담방 찾기 또는 생성
    conversation = get_or_create_conversation(
        organization_id=organization_id,
        session_id=session_id,
        channel="web_chat",
    )

    conversation_id = conversation["id"]

    # 2. 이후 노드에서 사용할 conversation_id 저장
    state["conversation_id"] = conversation_id

    # 3. 상담방의 AI 자동응답 상태 저장
    #    기존 row에 ai_enabled가 없거나 None이면 기본값 True로 처리한다.
    state["ai_enabled"] = conversation.get("ai_enabled", True)
    if state["ai_enabled"] is None:
        state["ai_enabled"] = True

    # 4. 고객 메시지 저장
    saved_message = create_conversation_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        sender_type="customer",
        sender_name="Customer",
        message=user_message,
        metadata={
            "session_id": session_id,
        },
    )

    if saved_message is None:
        # 메시지 저장에 실패하면 last_message만 갱신해
        # 목록과 실제 메시지 내역이 어긋나는 상황을 만들지 않는다.
        print(
            f"Failed to save customer message: "
            f"organization_id={organization_id}, conversation_id={conversation_id}"
        )
        return state

    # 5. 상담방 목록의 마지막 메시지 업데이트
    update_conversation_last_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        last_message=user_message,
    )

    return state