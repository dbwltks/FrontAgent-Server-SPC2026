import asyncio

from app.graph.state import AgentState
from app.graph.prompt_builder import (
    build_response_instructions,
    build_rule_instructions_text,
)
from app.providers.openai_provider import generate_text
from app.repositories.conversation_repo import list_conversation_messages


HISTORY_LIMIT = 10  # 최근 N개 메시지만 컨텍스트로 사용


async def response_node(state: AgentState) -> AgentState:
    intent = state.get("intent")

    rules = state.get("rules", [])
    applied_rules = state.get("applied_rules", [])

    knowledge_context = state.get("knowledge_context", [])
    knowledge_context_groups = state.get("knowledge_context_groups", [])
    session_state = state.get("session_state")

    user_message = state["user_message"]
    organization_id = state["organization_id"]
    conversation_id = state.get("conversation_id")

    # Supabase에서 이전 대화 히스토리 조회
    conversation_history = []

    if conversation_id:
        raw_messages = await asyncio.to_thread(
            list_conversation_messages,
            organization_id=organization_id,
            conversation_id=conversation_id,
            limit=HISTORY_LIMIT,
        )

        # sender_type → OpenAI role 변환
        # customer = user
        # ai = assistant
        for msg in raw_messages:
            sender = msg.get("sender_type")
            message = msg.get("message")

            if not message:
                continue

            if sender == "customer":
                conversation_history.append(
                    {
                        "role": "user",
                        "content": message,
                    }
                )

            elif sender == "ai":
                conversation_history.append(
                    {
                        "role": "assistant",
                        "content": message,
                    }
                )

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

    final_response = await generate_text(
        instructions=instructions,
        user_message=user_message,
        conversation_history=conversation_history or None,
    )

    state["final_response"] = final_response

    return state