import time

from app.graph.state import AgentState
from app.repositories.conversation_repo import get_or_create_conversation


# conversation_id/ai_enabled는 같은 (organization_id, session_id) 안에서 거의
# 바뀌지 않는데, get_or_create_conversation을 매 턴 호출하면 DB round-trip이
# 응답 첫 토큰 전체를 지연시킨다. rule_repo.get_active_rules와 같은 성격의
# 짧은 TTL 인메모리 캐시로 충분하다. ai_enabled가 관리자 화면에서 꺼지면
# 최대 TTL만큼 늦게 반영될 수 있다는 점만 trade-off로 남는다.
_CONVERSATION_CACHE_TTL_SECONDS = 30
_conversation_cache: dict[tuple[str, str], tuple[float, dict]] = {}


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


def ensure_conversation_for_session(
    organization_id: str,
    session_id: str,
    channel: str = "web_chat",
) -> dict:
    """finalize 등 응답 이후 persistence 경로에서 상담방을 확보할 때 사용한다."""
    return _get_or_create_conversation_cached(organization_id, session_id, channel)


def _lookup_conversation_cached(organization_id: str, session_id: str) -> dict | None:
    cache_key = (organization_id, session_id)
    cached = _conversation_cache.get(cache_key)
    now = time.monotonic()

    if cached is not None and now - cached[0] < _CONVERSATION_CACHE_TTL_SECONDS:
        return cached[1]
    return None


def conversation_node(state: AgentState) -> dict:
    """
    이번 턴 사용자 메시지를 state(messages)에만 반영한다.

    상담방 조회/생성과 DB 저장은 응답 경로를 막지 않도록 prepare_node
    백그라운드에서 처리한다. ai_enabled 분기만을 위해 캐시에 있을 때만
    동기 조회한다. 캐시 miss(새 세션)는 ai_enabled=True로 가정하고, 실제
    상담방 생성은 persistence 백그라운드에서 끝낸다.
    """
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]
    channel = state.get("channel", "web_chat")

    conversation = _lookup_conversation_cached(organization_id, session_id)
    if conversation is not None:
        conversation_id = conversation["id"]
        ai_enabled = conversation.get("ai_enabled", True)
        if ai_enabled is None:
            ai_enabled = True
    else:
        conversation_id = None
        ai_enabled = True

    return {
        "conversation_id": conversation_id,
        "ai_enabled": ai_enabled,
        "messages": [{"role": "user", "content": user_message}],
    }