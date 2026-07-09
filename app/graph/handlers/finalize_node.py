import logging
import threading

from app.graph.handlers.conversation_node import ensure_conversation_for_session
from app.graph.state import AgentState
from app.repositories.agent_run_repo import create_agent_run
from app.repositories.conversation_repo import (
    close_conversation,
    create_conversation_message,
    end_call_conversation,
    update_conversation_last_message,
)
from app.repositories.knowledge_repo import increment_reference_counts
from app.repositories.rule_repo import get_active_rules


logger = logging.getLogger(__name__)

VOICE_CHANNELS = frozenset({"web_call", "voice"})


def _save_ai_message(
    state: AgentState,
    *,
    conversation_id: str | None = None,
    applied_rules: list[str] | None = None,
) -> None:
    organization_id = state["organization_id"]
    resolved_conversation_id = conversation_id or state.get("conversation_id")
    final_response = state.get("final_response")

    if not resolved_conversation_id or not final_response:
        return

    metadata = {
        "intent": state.get("intent"),
        "applied_rules": applied_rules if applied_rules is not None else state.get("applied_rules", []),
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
        conversation_id=resolved_conversation_id,
        sender_type="ai",
        sender_name="Callbee",
        message=final_response,
        metadata=metadata,
    )

    if saved_message is None:
        logger.warning(
            "Failed to save AI message: organization_id=%s, conversation_id=%s",
            organization_id,
            resolved_conversation_id,
        )
        return

    try:
        update_conversation_last_message(
            organization_id=organization_id,
            conversation_id=resolved_conversation_id,
            last_message=final_response,
        )
    except Exception:
        logger.warning("Failed to update AI last_message", exc_info=True)


def _save_customer_message(state: AgentState, *, conversation_id: str) -> None:
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]
    log_message = (state.get("log_message") or user_message).strip()
    channel = state.get("channel", "web_chat")

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


def _persist_turn(state: AgentState) -> None:
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    channel = state.get("channel", "web_chat")

    applied_rules = state.get("applied_rules")
    if not applied_rules:
        applied_rules = [rule.get("name", "unnamed_rule") for rule in get_active_rules(organization_id)]

    conversation = ensure_conversation_for_session(
        organization_id=organization_id,
        session_id=session_id,
        channel=channel,
    )
    conversation_id = conversation["id"]

    _save_customer_message(state, conversation_id=conversation_id)
    _save_ai_message(
        state,
        conversation_id=conversation_id,
        applied_rules=applied_rules,
    )
    _end_session_if_requested(state, conversation_id=conversation_id)
    _save_agent_run(state, applied_rules=applied_rules)
    _increment_knowledge_references(state)


def _end_session_if_requested(state: AgentState, *, conversation_id: str | None = None) -> None:
    if not state.get("should_end_session"):
        return

    organization_id = state["organization_id"]
    session_id = state["session_id"]
    resolved_conversation_id = conversation_id or state.get("conversation_id")
    channel = state.get("channel", "web_chat")

    try:
        if channel in VOICE_CHANNELS:
            end_call_conversation(
                organization_id=organization_id,
                session_id=session_id,
            )
        elif resolved_conversation_id:
            close_conversation(
                organization_id=organization_id,
                conversation_id=resolved_conversation_id,
            )
    except Exception:
        logger.warning(
            "end_session handling failed: organization_id=%s session_id=%s conversation_id=%s channel=%s",
            organization_id,
            session_id,
            resolved_conversation_id,
            channel,
            exc_info=True,
        )


def _save_agent_run(state: AgentState, *, applied_rules: list[str] | None = None) -> None:
    payload = {
        "organization_id": state["organization_id"],
        "session_id": state["session_id"],
        "user_message": state["user_message"],
        "intent": state.get("intent"),
        "applied_rules": applied_rules if applied_rules is not None else state.get("applied_rules", []),
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


def schedule_turn_persistence(state: AgentState) -> None:
    """응답 반환 이후 백그라운드에서 규칙 조회·DB 저장을 실행한다."""
    snapshot = dict(state)

    threading.Thread(target=_persist_turn, args=(snapshot,), daemon=True).start()
