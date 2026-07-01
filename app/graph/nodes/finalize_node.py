import logging
import threading

from app.graph.nodes.conversation_node import invalidate_conversation_cache
from app.graph.state import AgentState
from app.repositories.agent_run_repo import create_agent_run
from app.repositories.conversation_repo import (
    close_conversation,
    create_conversation_message,
    end_call_conversation,
    update_conversation_last_message,
)
from app.repositories.knowledge_repo import increment_reference_counts
from app.tasks.repository import TaskRepository


logger = logging.getLogger(__name__)

VOICE_CHANNELS = frozenset({"web_call", "voice"})


def _save_ai_message(state: AgentState) -> None:
    organization_id = state["organization_id"]
    conversation_id = state.get("conversation_id")
    final_response = state.get("final_response")

    if not conversation_id or not final_response:
        return

    metadata = {
        "intent": state.get("intent"),
        "applied_rules": state.get("applied_rules", []),
        "used_knowledge": state.get("used_knowledge", []),
    }

    task_result = state.get("task_result")
    if task_result:
        metadata["task"] = {
            "flow_id": task_result.get("flow_id"),
            "task_session_id": task_result.get("task_session_id"),
            "current_node_key": task_result.get("current_node_key"),
            "status": task_result.get("status"),
            "error": task_result.get("error"),
            "trace": task_result.get("trace"),
        }

    saved_message = create_conversation_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        sender_type="ai",
        sender_name="Front Agent",
        message=final_response,
        metadata=metadata,
    )

    if saved_message is None:
        logger.warning(
            "Failed to save AI message: organization_id=%s, conversation_id=%s",
            organization_id,
            conversation_id,
        )
        return

    try:
        update_conversation_last_message(
            organization_id=organization_id,
            conversation_id=conversation_id,
            last_message=final_response,
        )
    except Exception:
        logger.warning("Failed to update AI last_message", exc_info=True)


def _end_session_if_requested(state: AgentState) -> None:
    if not state.get("should_end_session"):
        return

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
        TaskRepository().cancel_active_sessions(
            organization_id=organization_id,
            session_id=session_id,
        )
        invalidate_conversation_cache(organization_id, session_id)
    except Exception:
        logger.warning(
            "end_session handling failed: organization_id=%s session_id=%s conversation_id=%s channel=%s",
            organization_id,
            session_id,
            conversation_id,
            channel,
            exc_info=True,
        )


def _save_agent_run(state: AgentState) -> None:
    payload = {
        "organization_id": state["organization_id"],
        "session_id": state["session_id"],
        "user_message": state["user_message"],
        "intent": state.get("intent"),
        "applied_rules": state.get("applied_rules", []),
        "used_knowledge": state.get("used_knowledge", []),
        "final_response": state.get("final_response"),
        "status": "success",
        "error_message": None,
    }

    try:
        create_agent_run(**payload)
    except Exception:
        logger.warning(
            "Failed to save agent run log: organization_id=%s, session_id=%s",
            payload["organization_id"],
            payload["session_id"],
            exc_info=True,
        )


def _increment_knowledge_references(state: AgentState) -> None:
    source_ids = [
        item.get("source_id")
        for item in state.get("used_knowledge", [])
        if item.get("source_id")
    ]
    if not source_ids:
        return

    try:
        increment_reference_counts(source_ids)
    except Exception:
        logger.warning(
            "Failed to increment knowledge reference counts: organization_id=%s",
            state["organization_id"],
            exc_info=True,
        )


def finalize_node(state: AgentState) -> AgentState:
    """
    response_node 이후 마무리 작업(AI 메시지 저장, 세션 종료 처리, agent run 로그
    저장)을 한 노드로 합친다.

    기존에는 save_ai_message_node -> end_session_node -> save_agent_run_node로
    3개 노드를 거쳤는데, 각 노드 전환마다 LangGraph checkpointer가 Postgres에
    super-step을 write한다. 세 노드의 실제 작업은 이미 백그라운드 스레드라
    노드 본문 자체는 즉시 끝나므로, 노드를 합쳐 super-step(checkpoint write)
    횟수만 줄인다 — 로직은 그대로 재사용.

    end_session 처리는 메시지 저장과 달리 동기적으로 끝내야 할 이유가 없는
    백그라운드성 후처리이므로 같은 스레드에서 순서대로 묶어 실행한다.
    """

    def _run_finalize_tasks():
        _save_ai_message(state)
        _end_session_if_requested(state)
        _save_agent_run(state)
        _increment_knowledge_references(state)

    threading.Thread(target=_run_finalize_tasks, daemon=True).start()

    return state
