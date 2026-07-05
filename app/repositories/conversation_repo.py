from app.core.db import supabase
from datetime import datetime, timedelta, timezone

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
    insert_payload = {
        "organization_id": organization_id,
        "session_id": session_id,
        "channel": channel,
        "status": "open",
    }

    # 통화 채널은 첫 생성 시점을 통화 시작 시각으로 기록한다.
    if channel == "web_call":
        insert_payload["call_started_at"] = utc_now_iso()

    created = (
        supabase.table("conversations")
        .insert(insert_payload)
        .execute()
    )

    return created.data[0]


def end_call_conversation(
    organization_id: str,
    session_id: str,
) -> dict | None:
    """
    통화가 끝났을 때 call_ended_at/call_duration_seconds를 기록한다.
    call_started_at이 없으면(통화 채널이 아니었거나 이미 기록된 적 없으면) 손대지 않는다.
    """

    conversation = get_conversation_by_session(
        organization_id=organization_id,
        session_id=session_id,
    )

    if not conversation or not conversation.get("call_started_at"):
        return conversation

    if conversation.get("call_ended_at"):
        return conversation

    ended_at = datetime.now(timezone.utc)
    started_at = datetime.fromisoformat(conversation["call_started_at"])
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    duration_seconds = max(0, int((ended_at - started_at).total_seconds()))

    updated = (
        supabase.table("conversations")
        .update(
            {
                "call_ended_at": ended_at.isoformat(),
                "call_duration_seconds": duration_seconds,
                "status": "closed",
            }
        )
        .eq("organization_id", organization_id)
        .eq("session_id", session_id)
        .execute()
    )

    return updated.data[0] if updated.data else None


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
    channel: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    관리자 화면에서 상담방 목록을 조회한다.

    status를 넘기면 open/closed 등 특정 상태만, channel을 넘기면
    web_chat/web_call 등 특정 채널만 조회한다.
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

    if channel:
        query = query.eq("channel", channel)

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


def close_idle_calls(idle_minutes: int) -> int:
    """
    web_call 채널에서 last_message_at 기준 idle_minutes 이상 응답이 없는
    open 통화를 end_call_conversation과 동일하게 정리한다(닫은 개수 반환).
    클라이언트가 /voice/call/end를 못 호출한 경우(강제 종료 등)의 안전망이다.
    """

    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)
    ).isoformat()

    idle_rows = (
        supabase.table("conversations")
        .select("id, call_started_at")
        .eq("channel", "web_call")
        .eq("status", "open")
        .lt("last_message_at", cutoff)
        .execute()
    ).data or []

    if not idle_rows:
        return 0

    now = datetime.now(timezone.utc)
    closed_count = 0

    for row in idle_rows:
        update_payload = {
            "status": "closed",
            "updated_at": utc_now_iso(),
        }

        call_started_at = row.get("call_started_at")
        if call_started_at:
            started_at = datetime.fromisoformat(call_started_at)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            update_payload["call_ended_at"] = now.isoformat()
            update_payload["call_duration_seconds"] = max(
                0, int((now - started_at).total_seconds())
            )

        result = (
            supabase.table("conversations")
            .update(update_payload)
            .eq("id", row["id"])
            .execute()
        )
        if result.data:
            closed_count += 1

    return closed_count


WARNED_IDLE_MINUTES = 3
WARNED_TO_CLOSE_SECONDS = 10
IDLE_WARNING_MESSAGE = "아직 거기 계신가요? 잠시 더 기다려도 응답이 없으면 상담이 종료됩니다."
IDLE_CLOSED_MESSAGE = "응답이 없어 상담이 종료되었습니다. 다시 문의하실 일이 있으면 새로 말씀해 주세요."


def warn_or_close_idle_chats() -> None:
    """
    web_chat 상담방을 2단계로 정리한다.

    1. last_message_at 기준 WARNED_IDLE_MINUTES 지났고 아직 안내한 적 없으면
       안내 메시지를 남기고 idle_warned_at을 찍는다.
    2. 안내한 뒤 WARNED_TO_CLOSE_SECONDS 더 지나도 응답이 없으면(=last_message_at이
       안내 시점 이후로 갱신되지 않았으면) 종료 메시지를 남기고 closed로 바꾼다.
    """

    now = datetime.now(timezone.utc)
    warn_cutoff = (now - timedelta(minutes=WARNED_IDLE_MINUTES)).isoformat()

    open_conversations = (
        supabase.table("conversations")
        .select("id, organization_id, last_message_at, idle_warned_at")
        .eq("channel", "web_chat")
        .eq("status", "open")
        .lt("last_message_at", warn_cutoff)
        .execute()
    ).data or []

    for conversation in open_conversations:
        idle_warned_at = conversation.get("idle_warned_at")

        if not idle_warned_at:
            create_conversation_message(
                organization_id=conversation["organization_id"],
                conversation_id=conversation["id"],
                sender_type="system",
                message=IDLE_WARNING_MESSAGE,
            )
            supabase.table("conversations").update(
                {
                    "idle_warned_at": now.isoformat(),
                    "updated_at": utc_now_iso(),
                }
            ).eq("id", conversation["id"]).execute()
            continue

        warned_at = datetime.fromisoformat(idle_warned_at)
        if warned_at.tzinfo is None:
            warned_at = warned_at.replace(tzinfo=timezone.utc)

        # 안내 이후 사용자가 다시 말했으면 last_message_at이 안내 시점보다
        # 나중이므로 warn_cutoff 조건에 안 걸려 여기까지 오지 않는다.
        if (now - warned_at).total_seconds() < WARNED_TO_CLOSE_SECONDS:
            continue

        create_conversation_message(
            organization_id=conversation["organization_id"],
            conversation_id=conversation["id"],
            sender_type="system",
            message=IDLE_CLOSED_MESSAGE,
        )
        supabase.table("conversations").update(
            {
                "status": "closed",
                "updated_at": utc_now_iso(),
            }
        ).eq("id", conversation["id"]).execute()


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

def delete_conversation_messages(
    organization_id: str,
    conversation_id: str,
) -> None:
    (
        supabase.table("conversation_messages")
        .delete()
        .eq("organization_id", organization_id)
        .eq("conversation_id", conversation_id)
        .execute()
    )


def delete_conversation(
    organization_id: str,
    conversation_id: str,
) -> bool:
    """
    상담방과 하위 메시지를 삭제한다.
    """

    delete_conversation_messages(
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    result = (
        supabase.table("conversations")
        .delete()
        .eq("organization_id", organization_id)
        .eq("id", conversation_id)
        .execute()
    )

    return bool(result.data)