from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.repositories.conversation_repo import (
    list_conversations,
    get_conversation,
    get_conversation_by_session,
    list_conversation_messages,
    create_conversation_message,
    update_conversation_last_message,
    close_conversation,
    update_conversation_ai_enabled,
    delete_conversation,
)


router = APIRouter(tags=["Conversations"])


class AdminMessageRequest(BaseModel):
    message: str
    sender_name: str = "Admin"

class AIEnabledUpdateRequest(BaseModel):
    ai_enabled: bool


@router.get("/conversations")
def get_conversation_list(
    organization_id: str,
    status: str | None = None,
    channel: str | None = None,
    limit: int = 50,
):
    """
    관리자용 상담방 목록 조회 API.

    예:
    GET /conversations?organization_id=org_test
    GET /conversations?organization_id=org_test&status=open
    GET /conversations?organization_id=org_test&channel=web_call
    """

    conversations = list_conversations(
        organization_id=organization_id,
        status=status,
        channel=channel,
        limit=limit,
    )

    return {
        "organization_id": organization_id,
        "status": status,
        "channel": channel,
        "count": len(conversations),
        "items": conversations,
    }


@router.get("/conversations/by-session")
def get_conversation_by_session_api(
    organization_id: str,
    session_id: str,
):
    """
    위젯이 자신의 상담방을 polling으로 확인할 때 쓰는 API.

    상담방이 아직 없으면(첫 메시지 전송 전) 404를 반환한다.

    예:
    GET /conversations/by-session?organization_id=org_test&session_id=chat_123
    """

    conversation = get_conversation_by_session(
        organization_id=organization_id,
        session_id=session_id,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return conversation


@router.get("/conversations/{conversation_id}")
def get_conversation_detail(
    conversation_id: str,
    organization_id: str,
):
    """
    상담방 상세 정보 조회 API.
    """

    conversation = get_conversation(
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return conversation


@router.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: str,
    organization_id: str,
    limit: int = 100,
):
    """
    특정 상담방의 메시지 목록 조회 API.

    관리자 채팅방 상세 화면에서 사용한다.
    """

    messages = list_conversation_messages(
        organization_id=organization_id,
        conversation_id=conversation_id,
        limit=limit,
    )

    return {
        "organization_id": organization_id,
        "conversation_id": conversation_id,
        "count": len(messages),
        "items": messages,
    }


@router.post("/conversations/{conversation_id}/messages/admin")
def send_admin_message(
    conversation_id: str,
    organization_id: str,
    req: AdminMessageRequest,
):
    """
    관리자가 직접 상담방에 메시지를 남기는 API.

    나중에 AI 자동응답 OFF 상태에서 관리자가 고객에게 답장할 때 사용한다.
    지금은 DB 저장까지 먼저 구현한다.
    """

    conversation = get_conversation(
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    message = create_conversation_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        sender_type="admin",
        sender_name=req.sender_name,
        message=req.message,
        metadata={},
    )

    update_conversation_last_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        last_message=req.message,
    )

    return message


@router.patch("/conversations/{conversation_id}/close")
def close_conversation_api(
    conversation_id: str,
    organization_id: str,
):
    """
    상담방 종료 API.

    status를 closed로 바꾼다.
    """

    conversation = close_conversation(
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return conversation

@router.patch("/conversations/{conversation_id}/ai-enabled")
def update_ai_enabled(
    conversation_id: str,
    organization_id: str,
    req: AIEnabledUpdateRequest,
):
    """
    상담방의 AI 자동응답을 켜거나 끈다.

    관리자 화면에서 사용할 API.

    예:
    PATCH /conversations/{conversation_id}/ai-enabled?organization_id=org_test

    Body:
    {
      "ai_enabled": false
    }
    """

    conversation = update_conversation_ai_enabled(
        organization_id=organization_id,
        conversation_id=conversation_id,
        ai_enabled=req.ai_enabled,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return conversation

@router.delete("/conversations/{conversation_id}")
def delete_conversation_api(
    conversation_id: str,
    organization_id: str,
):
    """
    상담방(채팅/통화 내역)과 메시지를 삭제한다.
    """

    conversation = get_conversation(
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    deleted = delete_conversation(
        organization_id=organization_id,
        conversation_id=conversation_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete conversation",
        )

    return {
        "ok": True,
        "conversation_id": conversation_id,
    }