import logging

from langgraph.config import get_stream_writer

from app.graph.state import AgentState
from app.graph.message_utils import history_from_state_messages
from app.graph.prompt_builder import build_response_instructions
from app.providers.langchain_provider import stream_text
from app.repositories.organization_ai_settings_repo import get_ai_settings


logger = logging.getLogger(__name__)

FALLBACK_RESPONSE = "일시적인 오류로 답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."


async def response_node(state: AgentState) -> AgentState:
    """
    최종 응답을 생성한다.

    OpenAI 호출 자체는 항상 스트리밍으로 하고, 받은 delta를
    get_stream_writer()로 흘려보낸다. graph.astream(stream_mode="custom")으로
    호출되면 그 delta가 실시간으로 SSE에 전달되고, graph.ainvoke로
    호출되면 writer는 안전한 no-op이라 최종 텍스트만 모아서 반환한다.
    """
    intent = state.get("intent")
    organization_id = state["organization_id"]

    rules = state.get("rules", [])
    ai_settings = get_ai_settings(organization_id)

    knowledge_context = state.get("knowledge_context", [])
    knowledge_context_groups = state.get("knowledge_context_groups", [])

    user_message = state["user_message"]

    # checkpointer가 복원한 messages에서 직전까지의 히스토리를 재사용한다.
    conversation_history = history_from_state_messages(
        state.get("messages", []), exclude_current_turn=True
    )

    instructions = build_response_instructions(
        intent=intent,
        knowledge_context=knowledge_context,
        knowledge_context_groups=knowledge_context_groups,
        use_knowledge=state.get("use_knowledge", False),
        active_task=state.get("active_task"),
        task_step=state.get("task_step"),
        rules=rules,
        channel=state.get("channel", "web_chat"),
        voice_response_style=ai_settings.get("voice_response_style", "friendly_short"),
    )

    writer = get_stream_writer()
    chunks: list[str] = []

    try:
        async for delta in stream_text(
            organization_id=organization_id,
            instructions=instructions,
            input_text=user_message,
            conversation_history=conversation_history or None,
        ):
            if not delta:
                continue

            chunks.append(delta)
            writer({"type": "ai_response_delta", "delta": delta})
    except Exception:
        logger.warning("response_node LLM call failed", exc_info=True)
        if not chunks:
            chunks = [FALLBACK_RESPONSE]
            writer({"type": "ai_response_delta", "delta": FALLBACK_RESPONSE})

    final_response = "".join(chunks)

    state["final_response"] = final_response
    state["messages"] = [{"role": "assistant", "content": final_response}]

    return state
