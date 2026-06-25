import logging

from app.graph.state import AgentState
from app.repositories.conversation_repo import close_conversation, end_call_conversation


logger = logging.getLogger(__name__)

VOICE_CHANNELS = frozenset({"web_call", "voice"})


def end_session_node(state: AgentState) -> dict:
    """
    상담 종료 의도가 감지된 턴에서 conversations를 closed로 기록한다.
    - web_call/voice: call_ended_at·duration 포함
    - web_chat: status만 closed

    UI 전환(통화 끊기·채팅 입력 비활성화)은 프론트가 session_end SSE를 받은 뒤 처리한다.
    """
    if not state.get("should_end_session"):
        return {}

    organization_id = state["organization_id"]
    session_id = state["session_id"]
    conversation_id = state.get("conversation_id")
    channel = state.get("channel", "web_chat")

    try:
        if channel in VOICE_CHANNELS:
            end_call_conversation(
                organization_id=organization_id,
                session_id=session_id,
            )
        elif conversation_id:
            close_conversation(
                organization_id=organization_id,
                conversation_id=conversation_id,
            )
    except Exception:
        logger.warning(
            "end_session_node failed: organization_id=%s session_id=%s conversation_id=%s channel=%s",
            organization_id,
            session_id,
            conversation_id,
            channel,
            exc_info=True,
        )

    return {}
