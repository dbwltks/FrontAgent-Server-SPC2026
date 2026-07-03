from typing import Any

from langchain_core.messages import RemoveMessage

# checkpoint blob 크기/READ 지연을 줄이기 위한 설정
MAX_CHECKPOINT_MESSAGES = 24

# 다음 턴에 필요 없는 일회성 필드는 checkpoint에 남기지 않는다.
CHECKPOINT_EPHEMERAL_CLEAR: dict[str, Any] = {
    "knowledge_context": [],
    "knowledge_context_groups": [],
    "knowledge_queries": [],
    "used_knowledge": [],
    "rules": [],
    "applied_rules": [],
    "final_response": None,
    "intent": None,
    "next_action": None,
    "decision_reason": None,
    "use_knowledge": False,
    "should_use_knowledge": False,
    "should_end_session": False,
    "task_handled": False,
    "task_status": None,
    "knowledge_folder_id": None,
    "pending_task_prompt": None,
    "follow_up_response": None,
    "log_message": None,
    "active_task_session": None,
    "current_task_node": None,
    "current_task_flow_id": None,
    "current_task_node_key": None,
    "current_task_node_type": None,
    "task_route": None,
    "task_route_confidence": None,
    "task_route_reason": None,
}


def merge_turn_message_updates(conversation_update: dict, agent_update: dict) -> list:
    return (conversation_update.get("messages") or []) + (agent_update.get("messages") or [])


def build_checkpoint_message_updates(
    prior_messages: list | None,
    new_message_dicts: list,
    *,
    max_messages: int = MAX_CHECKPOINT_MESSAGES,
) -> list:
    if not new_message_dicts:
        return []

    prior = prior_messages or []
    if len(prior) + len(new_message_dicts) <= max_messages:
        return list(new_message_dicts)

    remove_count = len(prior) + len(new_message_dicts) - max_messages
    updates: list = []
    for message in prior[:remove_count]:
        message_id = getattr(message, "id", None)
        if message_id:
            updates.append(RemoveMessage(id=message_id))
    updates.extend(new_message_dicts)
    return updates


def slim_channel_values_for_checkpoint(channel_values: dict) -> dict:
    """Postgres checkpoint write 직전에 state blob 크기를 줄인다."""
    slim = {**channel_values, **CHECKPOINT_EPHEMERAL_CLEAR}
    messages = slim.get("messages")
    if isinstance(messages, list) and len(messages) > MAX_CHECKPOINT_MESSAGES:
        slim["messages"] = messages[-MAX_CHECKPOINT_MESSAGES:]
    return slim
