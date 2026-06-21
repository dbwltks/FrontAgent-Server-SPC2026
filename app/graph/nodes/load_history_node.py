from app.graph.state import AgentState
from app.repositories.conversation_repo import list_conversation_messages


HISTORY_LIMIT = 10  # 최근 N개 메시지만 컨텍스트로 사용


def load_history_node(state: AgentState) -> AgentState:
    """
    Supabase에서 최근 대화 히스토리를 한 번만 조회해 state에 캐싱한다.
    decision_node(의도 분류)와 response_node(응답 생성)가 같은 히스토리를 공유해
    중복 조회를 없앤다.
    """
    conversation_id = state.get("conversation_id")
    organization_id = state["organization_id"]

    conversation_history: list[dict] = []

    if conversation_id:
        raw_messages = list_conversation_messages(
            organization_id=organization_id,
            conversation_id=conversation_id,
            limit=HISTORY_LIMIT,
            latest=True,
        )

        # sender_type → OpenAI role 변환
        # customer = user, ai = assistant
        for msg in raw_messages:
            sender = msg.get("sender_type")
            message = msg.get("message")

            if not message:
                continue

            if sender == "customer":
                conversation_history.append({"role": "user", "content": message})
            elif sender == "ai":
                conversation_history.append({"role": "assistant", "content": message})

    state["conversation_history"] = conversation_history

    return state
