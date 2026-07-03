import asyncio

from app.graph.checkpoint_state import merge_turn_message_updates, slim_channel_values_for_checkpoint
from app.graph.nodes.agent_node import agent_node
from app.graph.nodes.ai_handoff_node import build_ai_handoff_update
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.finalize_node import schedule_turn_persistence
from app.graph.state import AgentState


async def prepare_node(state: AgentState) -> dict:
    """
    conversation_node와 agent_node를 병렬 실행한 뒤, persistence는 백그라운드로
    넘기고 checkpoint에는 슬림 state만 남긴다(super-step 1 + durability=exit).
    """
    conversation_update, agent_update = await asyncio.gather(
        asyncio.to_thread(conversation_node, state),
        agent_node(state),
    )

    if not conversation_update.get("ai_enabled", True):
        result = build_ai_handoff_update(conversation_update)
    else:
        result = {**conversation_update, **agent_update}
        result["messages"] = merge_turn_message_updates(conversation_update, agent_update)

    schedule_turn_persistence({**state, **result})
    return result
