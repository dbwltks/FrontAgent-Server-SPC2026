import logging
import threading
import time

from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    get_or_create_conversation,
    create_conversation_message,
    update_conversation_last_message,
)


logger = logging.getLogger(__name__)

# conversation_id/ai_enabled는 같은 (organization_id, session_id) 안에서 거의
# 바뀌지 않는데, get_or_create_conversation을 매 턴 호출하면 DB round-trip이
# 응답 첫 토큰 전체를 지연시킨다. rule_repo.get_active_rules와 같은 성격의
# 짧은 TTL 인메모리 캐시로 충분하다. ai_enabled가 관리자 화면에서 꺼지면
# 최대 TTL만큼 늦게 반영될 수 있다는 점만 trade-off로 남는다.
_CONVERSATION_CACHE_TTL_SECONDS = 30
_conversation_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def invalidate_conversation_cache(organization_id: str, session_id: str) -> None:
    _conversation_cache.pop((organization_id, session_id), None)


def _get_or_create_conversation_cached(organization_id: str, session_id: str, channel: str) -> dict:
    cache_key = (organization_id, session_id)
    cached = _conversation_cache.get(cache_key)
    now = time.monotonic()

    if cached is not None and now - cached[0] < _CONVERSATION_CACHE_TTL_SECONDS:
        return cached[1]

    conversation = get_or_create_conversation(
        organization_id=organization_id,
        session_id=session_id,
        channel=channel,
    )
    _conversation_cache[cache_key] = (now, conversation)
    return conversation


def conversation_node(state: AgentState) -> dict:
    """
    상담방을 찾거나 생성하고, 사용자 메시지를 저장한다.
    상담방의 ai_enabled 값을 state에 저장한다.

    진행 중 태스크 상세 조회(task_router 전용)는 더 이상 여기서 하지 않는다 -
    agent_node가 run_task tool을 호출하면 DynamicTaskRunner가 TaskRepository.
    find_active_session을 내부적으로 다시 조회하므로, 여기서 미리 조회해두는
    것은 agent_node와 병렬 실행될 때 활용되지도 못하는 중복 DB 호출이었다.
    """
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]

    log_message = (state.get("log_message") or user_message).strip()
    channel = state.get("channel", "web_chat")

    # 1. organization_id + session_id 기준으로 상담방 찾기 또는 생성 (짧은 TTL 캐시)
    conversation = _get_or_create_conversation_cached(
        organization_id=organization_id,
        session_id=session_id,
        channel=channel,
    )
    conversation_id = conversation["id"]

    # 2. 상담방의 AI 자동응답 상태
    ai_enabled = conversation.get("ai_enabled", True)
    if ai_enabled is None:
        ai_enabled = True

    # 3. 고객 메시지 저장은 응답 경로를 막지 않도록 백그라운드 처리
    def _save_customer_message():
        saved_message = create_conversation_message(
            organization_id=organization_id,
            conversation_id=conversation_id,
            sender_type="customer",
            sender_name="Customer",
            message=log_message,
            metadata={
                "session_id": session_id,
                "channel": channel,
                "agent_message": user_message if user_message != log_message else None,
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
                last_message=log_message,
            )
        except Exception:
            logger.warning("Failed to update customer last_message", exc_info=True)

    threading.Thread(target=_save_customer_message, daemon=True).start()

    return {
        "conversation_id": conversation_id,
        "ai_enabled": ai_enabled,
        "messages": [{"role": "user", "content": user_message}],
    }
