from collections.abc import Awaitable, Callable

from app.graph.state import AgentState

from app.graph.nodes.load_session_node import load_session_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.router_node import router_node
from app.graph.nodes.should_use_knowledge_node import should_use_knowledge_node
from app.graph.nodes.knowledge_node import knowledge_node
from app.graph.nodes.rule_node import rule_node
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

    HTTP /chat 그래프와 같은 흐름을 유지한다.

    흐름:
    1. Redis 세션 로드
    2. 상담방 생성/조회 + 고객 메시지 저장
    3. AI 자동응답 여부 확인
    4. intent 분류
    5. Knowledge 검색 필요 여부 판단
    6. 필요하면 Knowledge 검색
    7. rules 조회
    8. rules + knowledge + session context 기반 instructions 생성
    9. OpenAI streaming 응답 생성
    10. 최종 응답 저장
    11. Redis 세션 업데이트
    12. agent_runs 로그 저장
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
        state["rules"] = []
        state["applied_rules"] = []
        state["should_use_knowledge"] = False
        state["knowledge_context"] = []
        state["used_knowledge"] = []

        # 고객 메시지는 이미 conversation_node에서 저장됨
        # AI 메시지는 저장하지 않음
        return state

    # 4. 사용자 intent 분류
    state = router_node(state)

    # 5. Knowledge 검색 필요 여부 판단
    state = should_use_knowledge_node(state)

    # 6. Knowledge 검색
    # 명확히 필요 없는 질문이면 검색하지 않는다.
    # 그 외 질문은 검색을 시도하고, 실제 사용 여부는 retriever의 유사도 결과가 결정한다.
    if state.get("should_use_knowledge", False):
        state = knowledge_node(state)
    else:
        state["knowledge_context"] = []
        state["used_knowledge"] = []

    # 7. rules 조회
    # rules는 최종 응답 생성 직전에 조회한다.
    state = rule_node(state)

    # 8. streaming 응답용 instructions 생성
    # rules를 넘겨야 rule 이름뿐 아니라 실제 instruction까지 프롬프트에 들어간다.
    instructions = build_response_instructions(
        intent=state.get("intent"),
        rules=state.get("rules", []),
        applied_rules=state.get("applied_rules", []),
        knowledge_context=state.get("knowledge_context", []),
        session_state=state.get("session_state"),
    )

    # 9. Supabase에서 이전 대화 히스토리 조회
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

    # 10. OpenAI 응답을 delta 단위로 받기
    # 한 글자씩 보이는 streaming 방식은 그대로 유지한다.
    chunks: list[str] = []

    for delta in stream_text(
        instructions=instructions,
        input_text=state["user_message"],
        conversation_history=conversation_history or None,
    ):
        if not delta:
            continue

        chunks.append(delta)

        # delta가 생성될 때마다 WebSocket으로 전달
        await on_delta(delta)

    # 11. 전체 delta를 하나의 최종 응답으로 합치기
    state["final_response"] = "".join(chunks)

    # 12. 완성된 AI 응답을 conversation_messages에 저장
    state = save_ai_message_node(state)

    # 13. Redis 세션 상태 업데이트
    state = update_session_node(state)

    # 14. agent_runs 실행 로그 저장
    state = save_agent_run_node(state)

    return state