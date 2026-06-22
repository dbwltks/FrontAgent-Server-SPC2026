import logging
import threading

from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    get_or_create_conversation,
    create_conversation_message,
    update_conversation_last_message,
)


logger = logging.getLogger(__name__)


def conversation_node(state: AgentState) -> dict:
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

    decision_node와 병렬(같은 superstep)로 실행되므로, 자신이 바꾸지 않는
    키(organization_id 등)는 절대 포함하지 않고 변경분만 dict로 반환해야 한다.
    그렇지 않으면 두 노드가 같은 키에 동시에 값을 쓰는 것으로 인식되어
    LangGraph가 InvalidUpdateError를 낸다.
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

    # 2. 상담방의 AI 자동응답 상태.
    #    기존 row에 ai_enabled가 없거나 None이면 기본값 True로 처리한다.
    ai_enabled = conversation.get("ai_enabled", True)
    if ai_enabled is None:
        ai_enabled = True

    # 3. 고객 메시지 저장(관리자 UI/로그용)은 응답 경로에 필요하지 않으므로
    #    백그라운드로 던진다. 이 노드는 LangGraph executor의 별도 스레드에서
    #    실행되어 실행 중인 이벤트 루프가 없으므로 asyncio.create_task는 쓸 수
    #    없고, daemon Thread로 백그라운드 실행한다.
    def _save_customer_message():
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
            return

        try:
            update_conversation_last_message(
                organization_id=organization_id,
                conversation_id=conversation_id,
                last_message=user_message,
            )
        except Exception:
            logger.warning("Failed to update customer last_message", exc_info=True)

    threading.Thread(target=_save_customer_message, daemon=True).start()

    # 4. 이후 노드에서 사용할 conversation_id/ai_enabled 저장,
    #    LLM 멀티턴 메모리(checkpointer)에 사용자 메시지 추가
    return {
        "conversation_id": conversation_id,
        "ai_enabled": ai_enabled,
        "messages": [{"role": "user", "content": user_message}],
    }
