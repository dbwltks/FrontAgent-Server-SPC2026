import json
import logging
from typing import Any

import redis as redis_lib

from app.core.redis import redis_client


logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 60 * 60 * 24  # 24시간


def get_session_key(organization_id: str, session_id: str) -> str:
    return f"session:{organization_id}:{session_id}"


def get_session_state(organization_id: str, session_id: str) -> dict[str, Any]:
    """
    Redis는 세션 상태용 캐시 계층일 뿐이라 장애가 나도 채팅 자체가 죽으면 안 된다.
    조회 실패 시 빈 세션으로 취급해 대화는 계속 진행하고, 멀티턴 task 상태만 잃는다.
    """
    key = get_session_key(organization_id, session_id)

    try:
        data = redis_client.get(key)
    except redis_lib.RedisError:
        logger.warning("Redis unavailable, falling back to empty session state", exc_info=True)
        return {}

    if not data:
        return {}

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {}


def save_session_state(
    organization_id: str,
    session_id: str,
    state: dict[str, Any],
) -> None:
    key = get_session_key(organization_id, session_id)

    try:
        redis_client.setex(
            key,
            SESSION_TTL_SECONDS,
            json.dumps(state, ensure_ascii=False),
        )
    except redis_lib.RedisError:
        logger.warning("Redis unavailable, skipping session state save", exc_info=True)


def delete_session_state(organization_id: str, session_id: str) -> None:
    key = get_session_key(organization_id, session_id)

    try:
        redis_client.delete(key)
    except redis_lib.RedisError:
        logger.warning("Redis unavailable, skipping session state delete", exc_info=True)