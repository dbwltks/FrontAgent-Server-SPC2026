from app.core.db import supabase
from datetime import datetime, timezone

def utc_now_iso() -> str:
    """
    Supabase timestamp 컬럼에 넣기 좋은 UTC ISO 문자열을 만든다.
    """

    return datetime.now(timezone.utc).isoformat()

def get_or_create_conversation(
    organization_id: str,
    session_id: str,
    channel: str = "web_chat",
) -> dict:
    """
    organization_id + session_id 기준으로 상담방을 찾는다.

    이미 상담방이 있으면 기존 상담방을 반환하고,
    없으면 새 상담방을 생성한다.
    """

    # 1. 기존 상담방 조회
    existing = (
        supabase.table("conversations")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        return existing.data[0]

    # 2. 없으면 새 상담방 생성
    created = (
        supabase.table("conversations")
        .insert(
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "channel": channel,
                "status": "open",
            }
        )
        .execute()
    )

    return created.data[0]


def get_conversation_by_session(
    organization_id: str,
    session_id: str,
) -> dict | None:
    """
    organization_id + session_id 기준으로 상담방을 조회한다.

    get_or_create_conversation과 달리 없으면 생성하지 않고 None을 반환한다.
    위젯이 자신의 상담방 상태(관리자 메시지 등)를 polling으로 확인할 때 쓴다.
    """

    existing = (
        supabase.table("conversations")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )

    return existing.data[0] if existing.data else None


def create_conversation_message(
    organization_id: str,
    conversation_id: str,
    sender_type: str,
    message: str,
    sender_name: str | None = None,
    metadata: dict | None = None,
) -> dict | None:
    """
    상담방에 메시지를 저장한다.

    sender_type:
    - customer: 고객 메시지
    - ai: AI 응답
    - admin: 관리자 메시지
    - system: 시스템 알림
    """

    result = (
        supabase.table("conversation_messages")
        .insert(
            {
                "organization_id": organization_id,
                "conversation_id": conversation_id,
                "sender_type": sender_type,
                "sender_name": sender_name,
                "message": message,
                "metadata": metadata or {},
            }
        )
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def update_conversation_last_message(
    organization_id: str,
    conversation_id: str,
    last_message: str,
) -> dict | None:
    """
    상담방 목록에서 보여줄 마지막 메시지와 시간을 업데이트한다.
    """

    now = utc_now_iso()

    result = (
        supabase.table("conversations")
        .update(
            {
                "last_message": last_message,
                "last_message_at": now,
                "updated_at": now,
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", conversation_id)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def list_conversations(
    organization_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    관리자 화면에서 상담방 목록을 조회한다.

    status를 넘기면 open/closed 등 특정 상태만 조회한다.
    """

    query = (
        supabase.table("conversations")
        .select("*")
        .eq("organization_id", organization_id)
        .order("last_message_at", desc=True)
        .limit(limit)
    )

    if status:
        query = query.eq("status", status)

    result = query.execute()

    return result.data or []


def get_conversation(
    organization_id: str,
    conversation_id: str,
) -> dict | None:
    """
    상담방 하나를 상세 조회한다.
    """

    result = (
        supabase.table("conversations")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", conversation_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def list_conversation_messages(
    organization_id: str,
    conversation_id: str,
    limit: int = 100,
    latest: bool = False,
) -> list[dict]:
    """
    특정 상담방의 메시지 목록을 조회한다.

    채팅방 상세 화면에서 사용한다.

    latest=True면 가장 최근 메시지 limit개를 가져온 뒤
    시간순(오래된 것 → 최신)으로 정렬해 반환한다.
    AI 응답 생성용 history 조회에 사용한다.
    """

    query = (
        supabase.table("conversation_messages")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("conversation_id", conversation_id)
    )

    if latest:
        result = query.order("created_at", desc=True).limit(limit).execute()
        return list(reversed(result.data or []))

    result = query.order("created_at").limit(limit).execute()
    return result.data or []


def close_conversation(
    organization_id: str,
    conversation_id: str,
) -> dict | None:
    """
    상담방 상태를 closed로 변경한다.
    """

    result = (
        supabase.table("conversations")
        .update(
            {
                "status": "closed",
                "updated_at": utc_now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", conversation_id)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]

def update_conversation_ai_enabled(
    organization_id: str,
    conversation_id: str,
    ai_enabled: bool,
) -> dict | None:
    """
    상담방의 AI 자동응답 상태를 변경한다.

    ai_enabled = True
    - AI가 자동으로 응답한다.

    ai_enabled = False
    - 고객 메시지는 저장하지만 AI 응답은 생성하지 않는다.
    - 관리자가 직접 응답해야 한다.
    """

    result = (
        supabase.table("conversations")
        .update(
            {
                "ai_enabled": ai_enabled,
                "updated_at": utc_now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", conversation_id)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]