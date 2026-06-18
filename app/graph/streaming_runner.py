from collections.abc import Awaitable, Callable

from app.graph.state import AgentState
from app.graph.nodes.load_session_node import load_session_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.router_node import router_node
from app.graph.nodes.rule_node import rule_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.save_ai_message_node import save_ai_message_node
from app.graph.nodes.update_session_node import update_session_node
from app.graph.nodes.save_agent_run_node import save_agent_run_node
from app.graph.prompt_builder import build_response_instructions
from app.providers.openai_streaming_provider import stream_text
from app.repositories.conversation_repo import list_conversation_messages

HISTORY_LIMIT = 10


async def run_agent_streaming(
    initial_state: AgentState,
    on_delta: Callable[[str], Awaitable[None]],
) -> AgentState:
    """
    WebSocket streaming 전용 Agent Runner.
    """

    state = initial_state

    # 1. Redis 세션 로드
    state = load_session_node(state)

    # 2. 상담방 생성/조회 + 고객 메시지 저장
    state = conversation_node(state)

    # 3. AI 자동응답이 꺼져 있으면 여기서 중단
    if not state.get("ai_enabled", True):
        state["intent"] = "handoff"
        state["final_response"] = None
        state["applied_rules"] = []
        state["knowledge_context"] = []
        state["used_knowledge"] = []

        # 고객 메시지는 이미 저장됨.
        # AI 메시지는 저장하지 않음.
        # agent_runs에는 "skipped" 상태로 남기고 싶으면 아래 노드를 개선해서 저장 가능.
        return state

    # 4. 사용자 intent 분류
    state = router_node(state)

    # 5. 등록 지식 RAG 검색
    state = knowledge_node(state)

    # 6. rules 조회
    # 최종 답변 생성 직전에 관리자가 등록한 응답 규칙을 가져온다.
    state = rule_node(state)

    # 7. streaming 응답용 instructions 생성
    instructions = build_response_instructions(
        intent=state.get("intent"),
        rules=state.get("rules", []),
        applied_rules=state.get("applied_rules", []),
        knowledge_context=state.get("knowledge_context", []),
        session_state=state.get("session_state"),
    )

    # Supabase에서 이전 대화 히스토리 조회
    conversation_history = []
    conversation_id = state.get("conversation_id")
    if conversation_id:
        raw_messages = list_conversation_messages(
            organization_id=state["organization_id"],
            conversation_id=conversation_id,
            limit=HISTORY_LIMIT,
        )
        for msg in raw_messages:
            sender = msg.get("sender_type")
            if sender == "customer":
                conversation_history.append({"role": "user", "content": msg["message"]})
            elif sender == "ai":
                conversation_history.append({"role": "assistant", "content": msg["message"]})

    # 8. OpenAI 응답을 delta 단위로 받기
    chunks: list[str] = []

    for delta in stream_text(
        instructions=instructions,
        input_text=state["user_message"],
        conversation_history=conversation_history or None,
    ):
        chunks.append(delta)

        # delta가 생성될 때마다 WebSocket으로 전달
        await on_delta(delta)

    # 9. 전체 delta를 하나의 최종 응답으로 합치기
    state["final_response"] = "".join(chunks)

    # 10. 완성된 AI 응답을 conversation_messages에 저장
    state = save_ai_message_node(state)

    # 11. Redis 세션 상태 업데이트
    state = update_session_node(state)

    # 12. agent_runs 실행 로그 저장
    state = save_agent_run_node(state)

    return state