import logging
import threading
from typing import Any

from app.graph.state import AgentState
from app.repositories.conversation_repo import (
    get_or_create_conversation,
    create_conversation_message,
    update_conversation_last_message,
)
from app.tasks.repository import TaskRepository


logger = logging.getLogger(__name__)


def _empty_task_context() -> dict[str, Any]:
    return {
        "has_active_task": False,
        "active_task_session": None,
        "current_task_node": None,
        "current_task_flow_id": None,
        "current_task_node_key": None,
        "current_task_node_type": None,
        "pending_task_prompt": None,
    }


def _extract_pending_task_prompt(node: dict[str, Any] | None) -> str | None:
    if not node:
        return None

    config = node.get("config") or {}

    return (
        config.get("message")
        or config.get("prompt")
        or config.get("question")
        or config.get("text")
    )


def _get_active_task_context(
    organization_id: str,
    session_id: str,
) -> dict[str, Any]:
    """
    진행 중 task_session이 있으면,
    task_router_node가 판단에 사용할 수 있도록 현재 task node 정보까지 조회한다.

    여기서는 상품명/가격/반려동물 같은 비즈니스 키워드를 하드코딩하지 않는다.
    단지 현재 태스크가 어떤 노드에서 무엇을 기다리는지만 state에 담는다.
    """
    try:
        repository = TaskRepository()

        active_session = repository.find_active_session(
            organization_id=organization_id,
            session_id=session_id,
        )

        if not active_session:
            return _empty_task_context()

        flow_id = active_session.get("flow_id")

        # waiting_node_key가 있으면 사용자가 입력해야 하는 대기 노드가 더 명확하다.
        # 없으면 current_node_key를 사용한다.
        current_node_key = (
            active_session.get("waiting_node_key")
            or active_session.get("current_node_key")
        )

        current_node = None
        if flow_id and current_node_key:
            current_node = repository.get_node_by_key(
                flow_id=flow_id,
                node_key=current_node_key,
            )

        return {
            "has_active_task": True,
            "active_task_session": active_session,
            "current_task_node": current_node,
            "current_task_flow_id": flow_id,
            "current_task_node_key": current_node_key,
            "current_task_node_type": (
                current_node.get("node_type") if current_node else None
            ),
            "pending_task_prompt": _extract_pending_task_prompt(current_node),
        }

    except Exception:
        logger.warning("Failed to load active task context", exc_info=True)
        return _empty_task_context()


def conversation_node(state: AgentState) -> dict:
    """
    상담방을 찾거나 생성하고, 사용자 메시지를 저장한다.

    추가 역할:
    - conversation.ai_enabled 값을 state에 저장한다.
    - 진행 중 task_session이 있으면 현재 task node 정보까지 state에 저장한다.
    """
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]

    log_message = (state.get("log_message") or user_message).strip()
    channel = state.get("channel", "web_chat")

    # 1. organization_id + session_id 기준으로 상담방 찾기 또는 생성
    conversation = get_or_create_conversation(
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

    # 4. 진행 중 태스크 상세 context 조회
    task_context = _get_active_task_context(
        organization_id=organization_id,
        session_id=session_id,
    )

    # 5. 이후 노드에서 사용할 state 변경분 반환
    return {
        "conversation_id": conversation_id,
        "ai_enabled": ai_enabled,
        "messages": [{"role": "user", "content": user_message}],
        **task_context,
    }