import logging
import threading

from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    create_conversation_message,
    update_conversation_last_message,
)


logger = logging.getLogger(__name__)


def save_ai_message_node(state: AgentState) -> AgentState:
    """
    AI 최종 응답을 conversation_messages에 저장한다 (관리자 UI/로그용).
    멀티턴 LLM 메모리는 response_node가 이미 state["messages"]에 추가했고,
    checkpointer가 별도로 영속화하므로 이 노드와 무관하다.

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
        logger.warning(
            "Failed to save AI message: organization_id=%s, conversation_id=%s",
            organization_id,
            conversation_id,
        )
        return state

    # 2. 상담방 목록의 마지막 메시지 업데이트는 응답 경로와 무관한 부수 효과이므로
    #    백그라운드로 던진다. 이 노드는 LangGraph가 별도 스레드(run_in_executor)에서
    #    동기로 실행하므로 실행 중인 이벤트 루프가 없어 별도 스레드를 직접 띈다.
    threading.Thread(
        target=update_conversation_last_message,
        kwargs={
            "organization_id": organization_id,
            "conversation_id": conversation_id,
            "last_message": final_response,
        },
        daemon=True,
    ).start()

    return state