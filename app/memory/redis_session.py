import json
from typing import Any

from app.core.redis import redis_client


SESSION_TTL_SECONDS = 60 * 60 * 24  # 24시간


def get_session_key(organization_id: str, session_id: str) -> str:
    return f"session:{organization_id}:{session_id}"


def get_session_state(organization_id: str, session_id: str) -> dict[str, Any]:
    key = get_session_key(organization_id, session_id)
    data = redis_client.get(key)

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
    redis_client.setex(
        key,
        SESSION_TTL_SECONDS,
        json.dumps(state, ensure_ascii=False),
    )


def delete_session_state(organization_id: str, session_id: str) -> None:
    key = get_session_key(organization_id, session_id)
    redis_client.delete(key)