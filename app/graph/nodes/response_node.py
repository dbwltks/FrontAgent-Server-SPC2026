from app.graph.state import AgentState
from app.graph.prompt_builder import build_response_instructions
from app.providers.openai_provider import generate_text
from app.repositories.conversation_repo import list_conversation_messages

HISTORY_LIMIT = 10  # 최근 N개 메시지만 컨텍스트로 사용


def response_node(state: AgentState) -> AgentState:
    intent = state.get("intent")
    applied_rules = state.get("applied_rules", [])
    knowledge_context = state.get("knowledge_context", [])
    session_state = state.get("session_state")
    user_message = state["user_message"]
    organization_id = state["organization_id"]
    conversation_id = state.get("conversation_id")

    # Supabase에서 이전 대화 히스토리 조회
    conversation_history = []
    if conversation_id:
        raw_messages = list_conversation_messages(
            organization_id=organization_id,
            conversation_id=conversation_id,
            limit=HISTORY_LIMIT,
        )
        # sender_type → OpenAI role 변환 (customer=user, ai=assistant)
        for msg in raw_messages:
            sender = msg.get("sender_type")
            if sender == "customer":
                conversation_history.append({"role": "user", "content": msg["message"]})
            elif sender == "ai":
                conversation_history.append({"role": "assistant", "content": msg["message"]})

    instructions = build_response_instructions(
        intent=intent,
        applied_rules=applied_rules,
        knowledge_context=knowledge_context,
        session_state=session_state,
    )

    final_response = generate_text(
        instructions=instructions,
        user_message=user_message,
        conversation_history=conversation_history or None,
    )

    state["final_response"] = final_response

    return state