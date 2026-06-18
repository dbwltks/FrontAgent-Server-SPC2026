from collections.abc import Awaitable, Callable

from app.graph.state import AgentState

from app.graph.nodes.load_session_node import load_session_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.nodes.decision_node import decision_node
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

    HTTP /chat 그래프와 같은 decision_node 기반 흐름을 유지한다.

    흐름:
    1. Redis 세션 로드
    2. 상담방 생성/조회 + 고객 메시지 저장
    3. AI 자동응답 여부 확인
    4. decision_node에서 intent / next_action / task_type 판단
    5. next_action이 search_knowledge면 Knowledge 검색
    6. rules 조회
    7. rules + knowledge + session context 기반 instructions 생성
    8. OpenAI streaming 응답 생성
    9. 최종 응답 저장
    10. Redis 세션 업데이트
    11. agent_runs 로그 저장
    """
    state = initial_state

    # 1. Redis 세션 로드
    state = load_session_node(state)

    # 2. 상담방 생성/조회 + 고객 메시지 저장
    state = conversation_node(state)

    # 3. AI 자동응답이 꺼져 있으면 여기서 중단
    if not state.get("ai_enabled", True):
        state["intent"] = "handoff"
        state["next_action"] = "handoff"
        state["task_type"] = "none"
        state["use_knowledge"] = False
        state["decision_reason"] = "AI 자동응답이 꺼져 있어 관리자 응답 대기 상태로 전환"
        state["task_result"] = None
        state["should_use_knowledge"] = False
        state["final_response"] = None

        state["rules"] = []
        state["rule_instructions"] = ""
        state["applied_rules"] = []

        state["knowledge_context"] = []
        state["used_knowledge"] = []

        # 고객 메시지는 이미 conversation_node에서 저장됨
        # AI 메시지는 저장하지 않음
        return state

    # 4. decision_node에서 다음 처리 방향 판단
    state = decision_node(state)

    # 5. Knowledge 검색
    # decision_node가 search_knowledge를 선택한 경우에만 지식 검색을 실행한다.
    if state.get("next_action") == "search_knowledge" or state.get("use_knowledge", False):
        state = knowledge_node(state)
    else:
        state["knowledge_context"] = []
        state["used_knowledge"] = []

    # 6. rules 조회
    # rules는 최종 응답 생성 직전에 조회한다.
    state = rule_node(state)

    # 7. streaming 응답용 instructions 생성
    # rules를 넘겨야 rule 이름뿐 아니라 실제 instruction까지 프롬프트에 들어간다.
    instructions = build_response_instructions(
        intent=state.get("intent"),
        rules=state.get("rules", []),
        applied_rules=state.get("applied_rules", []),
        knowledge_context=state.get("knowledge_context", []),
        session_state=state.get("session_state"),
    )

    # 8. Supabase에서 이전 대화 히스토리 조회
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

    # 9. OpenAI 응답을 delta 단위로 받기
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

    # 10. 전체 delta를 하나의 최종 응답으로 합치기
    state["final_response"] = "".join(chunks)

    # 11. 완성된 AI 응답을 conversation_messages에 저장
    state = save_ai_message_node(state)

    # 12. Redis 세션 상태 업데이트
    state = update_session_node(state)

    # 13. agent_runs 실행 로그 저장
    state = save_agent_run_node(state)

    return state