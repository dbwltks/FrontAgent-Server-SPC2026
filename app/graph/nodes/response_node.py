import logging

from langgraph.config import get_stream_writer

from app.graph.state import AgentState
from app.graph.message_utils import history_from_state_messages
from app.graph.prompt_builder import build_response_instructions
from app.providers.langchain_provider import get_voice_response_style, stream_text


logger = logging.getLogger(__name__)

FALLBACK_RESPONSE = "일시적인 오류로 답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."

def _build_task_resume_message(state: dict) -> str | None:
    """
    태스크 진행 중 지식 질문에 답한 뒤,
    원래 태스크 단계로 복귀시키기 위한 안내 문구를 만든다.

    상품명/서비스명 키워드 하드코딩이 아니라,
    현재 task state의 pending_task_prompt를 기반으로 만든다.
    """
    has_active_task = state.get("has_active_task", False)
    use_knowledge = state.get("use_knowledge", False)
    next_action = state.get("next_action")

    if not has_active_task:
        return None

    if not use_knowledge:
        return None

    if next_action != "search_knowledge":
        return None

    pending_prompt = state.get("pending_task_prompt")

    if pending_prompt:
        return f"예약을 계속하려면 {pending_prompt}"

    return "예약을 계속하려면 원하시는 서비스를 선택해 주세요."


def _append_task_resume_message(response: str, state: dict) -> str:
    """
    LLM이 예약 복귀 안내를 빼먹어도 마지막에 반드시 붙인다.
    """
    resume_message = _build_task_resume_message(state)

    if not resume_message:
        return response

    if resume_message in response:
        return response

    return f"{response.rstrip()}\n\n{resume_message}"


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
    voice_response_style = await get_voice_response_style(organization_id)

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
        task_result=state.get("task_result"),

        # task_router_node가 판단한 태스크 중간 라우팅 정보
        has_active_task=state.get("has_active_task", False),
        task_route=state.get("task_route"),
        task_route_reason=state.get("task_route_reason"),
        pending_task_prompt=state.get("pending_task_prompt"),
        current_task_node_key=state.get("current_task_node_key"),

        rules=rules,
        channel=state.get("channel", "web_chat"),
        voice_response_style=voice_response_style,
        should_end_session=bool(state.get("should_end_session")),
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

    resume_message = _build_task_resume_message(state)
    if resume_message and resume_message not in final_response:
        suffix = f"\n\n{resume_message}"
        final_response = f"{final_response.rstrip()}{suffix}"

        # SSE 스트리밍 응답에도 예약 복귀 문구를 흘려보낸다.
        writer({"type": "ai_response_delta", "delta": suffix})

    state["final_response"] = final_response
    state["messages"] = [{"role": "assistant", "content": final_response}]

    return state
