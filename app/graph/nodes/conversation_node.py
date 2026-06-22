import logging

from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    get_or_create_conversation,
    create_conversation_message,
    update_conversation_last_message,
)


logger = logging.getLogger(__name__)


def conversation_node(state: AgentState) -> AgentState:
    """
    상담방을 찾거나 생성하고, 사용자 메시지를 두 곳에 기록한다.

    - Supabase conversation_messages: 관리자 화면/로그/검색용 영구 기록.
      멀티턴 LLM 컨텍스트 용도가 아니므로 응답 경로를 막지 않게 last_message
      갱신은 백그라운드로 던진다.
    - state["messages"]: LangGraph checkpointer가 thread_id 기준으로
      자동 영속화하는 멀티턴 대화 메모리. decision/response 노드가 여기서 읽는다.

    추가 역할:
    - conversation.ai_enabled 값을 state에 저장한다.
    - 이후 ai_handoff_node가 AI 응답 생성 여부를 판단할 수 있다.
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

    # 4. 고객 메시지 저장 (관리자 UI/로그용)
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
        logger.warning(
            "Failed to save customer message: organization_id=%s, conversation_id=%s",
            organization_id,
            conversation_id,
        )
    else:
        # 5. 이 노드는 LangGraph의 executor에서 실행되므로 동기 DB 갱신을 여기서
        #    완료한다. 요청마다 별도 Thread를 만들지 않아 동시 요청 시 Thread가
        #    무제한으로 증가하는 것을 방지한다.
        try:
            update_conversation_last_message(
                organization_id=organization_id,
                conversation_id=conversation_id,
                last_message=user_message,
            )
        except Exception:
            logger.warning("Failed to update customer last_message", exc_info=True)

    # 6. LLM 멀티턴 메모리(checkpointer)에 사용자 메시지 추가
    state["messages"] = [{"role": "user", "content": user_message}]

    return state
