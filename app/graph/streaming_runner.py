import asyncio
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

TraceStepCallback = Callable[[str, str, str, list], Awaitable[None]] | None


async def run_agent_streaming(
    initial_state: AgentState,
    on_delta: Callable[[str], Awaitable[None]],
    on_trace_step: TraceStepCallback = None,
) -> AgentState:
    state = initial_state

    async def emit(step: str, status: str, detail: str = "", items: list = []) -> None:
        if on_trace_step:
            await on_trace_step(step, status, detail, items)

    # 1. Redis 세션 로드
    state = load_session_node(state)

    # 2. 상담방 생성/조회 + 고객 메시지 저장
    await emit("conversation", "active", "대화 세션 확인 중")
    state = conversation_node(state)
    await emit("conversation", "done", f"conversation_id={state.get('conversation_id')}")

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
        return state

    # 4. decision_node에서 다음 처리 방향 판단
    await emit("intent", "active", "의도 분석 중")
    state = decision_node(state)
    await emit(
        "intent",
        "done",
        f"intent={state.get('intent')}",
        [state.get("decision_reason", "")],
    )

    # 5. Knowledge 검색
    next_action = state.get("next_action")
    if next_action in ("search_knowledge", "run_task") or state.get("use_knowledge", False):
        await emit("knowledge", "active", "지식 검색 중")
        state = knowledge_node(state)
        sources = [k.get("source_title", "") for k in state.get("used_knowledge", [])]
        await emit("knowledge", "done", f"{len(sources)}개 문서 참조", sources)
    else:
        state["knowledge_context"] = []
        state["used_knowledge"] = []

    # 6. rules 조회
    await emit("rules", "active", "규칙 평가 중")
    state = rule_node(state)
    rule_names = state.get("applied_rules", [])
    await emit("rules", "done", f"{len(rule_names)}개 규칙 적용", rule_names)

    # 7. streaming 응답용 instructions 생성
    instructions = build_response_instructions(
        intent=state.get("intent"),
        rules=state.get("rules", []),
        applied_rules=state.get("applied_rules", []),
        knowledge_context=state.get("knowledge_context", []),
        session_state=state.get("session_state"),
    )

    # 8. 이전 대화 히스토리 조회
    conversation_history = []
    conversation_id = state.get("conversation_id")

    if conversation_id:
        raw_messages = await asyncio.to_thread(
            list_conversation_messages,
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
                conversation_history.append({"role": "user", "content": message})
            elif sender == "ai":
                conversation_history.append({"role": "assistant", "content": message})

    # 9. OpenAI streaming 응답 생성
    await emit("response", "active", "AI 응답 생성 중")
    chunks: list[str] = []

    async for delta in stream_text(
        instructions=instructions,
        input_text=state["user_message"],
        conversation_history=conversation_history or None,
    ):
        if not delta:
            continue

        chunks.append(delta)
        await on_delta(delta)

    state["final_response"] = "".join(chunks)
    await emit("response", "done", "응답 생성 완료")

    # 10. AI 응답 저장
    state = save_ai_message_node(state)

    # 11. Redis 세션 업데이트
    state = update_session_node(state)

    # 12. agent_runs 로그 저장
    state = save_agent_run_node(state)

    return state
