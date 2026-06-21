import logging

from app.graph.state import AgentState
from app.graph.prompt_builder import (
    build_response_instructions,
    build_rule_instructions_text,
)
from app.providers.openai_provider import generate_text


logger = logging.getLogger(__name__)

FALLBACK_RESPONSE = "일시적인 오류로 답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."


async def response_node(state: AgentState) -> AgentState:
    intent = state.get("intent")

    rules = state.get("rules", [])
    applied_rules = state.get("applied_rules", [])

    knowledge_context = state.get("knowledge_context", [])
    knowledge_context_groups = state.get("knowledge_context_groups", [])
    session_state = state.get("session_state")

    user_message = state["user_message"]

    # load_history_node에서 이미 조회해둔 히스토리를 재사용한다.
    conversation_history = state.get("conversation_history", [])

    # rules 목록을 AI 프롬프트에 넣기 좋은 문자열로 변환
    rule_instructions = state.get("rule_instructions")

    if not rule_instructions:
        rule_instructions = build_rule_instructions_text(rules)
        state["rule_instructions"] = rule_instructions

    instructions = build_response_instructions(
        intent=intent,
        knowledge_context=knowledge_context,
        knowledge_context_groups=knowledge_context_groups,
        session_state=session_state,
        rules=rules,
        rule_instructions=rule_instructions,
        applied_rules=applied_rules,
    )

    try:
        final_response = await generate_text(
            instructions=instructions,
            user_message=user_message,
            conversation_history=conversation_history or None,
        )
    except Exception:
        logger.warning("response_node LLM call failed", exc_info=True)
        final_response = FALLBACK_RESPONSE

    state["final_response"] = final_response

    return state