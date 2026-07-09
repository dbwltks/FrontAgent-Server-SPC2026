import time
from typing import AsyncIterator, Callable, Awaitable

from app.graph.graph_runtime import build_initial_state, get_agent_graph, graph_config_for, graph_execution_kwargs
from app.graph.task_context import hydrate_task_result_for_response
from app.services.agent_stream import (
    AI_DISABLED_MESSAGE,
    elapsed_ms_since,
    stream_agent_graph_events,
)

# /chat, /voice, /web-call이 모두 같은 LangGraph(prepare -> agent -> response)를 거치는 공통 진입점.
# 각 API에 반복되던 "그래프 실행 -> 결과 추출" 패턴만 여기로 모은다.
# SSE 포맷이나 응답 스키마는 호출하는 쪽(chat.py, web_call.py 등)이 채널 특성에 맞게 감싼다.


async def run_agent_turn(
    *,
    organization_id: str,
    session_id: str,
    user_message: str,
    channel: str,
    knowledge_folder_id: str | None = None,
    log_message: str | None = None,
) -> dict:
    """
    그래프를 끝까지 실행하고 최종 state를 그대로 반환한다(비스트리밍).
    """
    agent_graph = get_agent_graph()
    initial_state = build_initial_state(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        knowledge_folder_id=knowledge_folder_id,
        channel=channel,
        log_message=log_message,
    )
    config = graph_config_for(organization_id, session_id)
    return await agent_graph.ainvoke(
        initial_state,
        config=config,
        **graph_execution_kwargs(),
    )


async def stream_agent_turn(
    *,
    organization_id: str,
    session_id: str,
    user_message: str,
    channel: str,
    knowledge_folder_id: str | None = None,
    log_message: str | None = None,
    on_delta: Callable[[str], Awaitable[None] | None] | None = None,
    on_node_update: Callable[[str, dict], Awaitable[None] | None] | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """
    그래프를 스트리밍으로 실행하며 (event, data) 튜플을 그대로 흘려보낸다.
    마지막 항목은 ("turn_end", {...})로, answer/follow_up_message/conversation_id/
    should_end_session처럼 호출자가 공통으로 필요한 필드를 추려서 담는다.

    on_delta/on_node_update는 stream_agent_graph_events에 그대로 전달되어
    채널별 부가 동작(예: web_call의 TTS 스케줄링)을 끼워넣을 수 있다.
    """
    agent_graph = get_agent_graph()
    initial_state = build_initial_state(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        knowledge_folder_id=knowledge_folder_id,
        channel=channel,
        log_message=log_message,
    )
    config = graph_config_for(organization_id, session_id)
    started_at = time.perf_counter()

    final_state: dict = {}
    async for event, data in stream_agent_graph_events(
        agent_graph=agent_graph,
        initial_state=initial_state,
        config=config,
        started_at=started_at,
        on_delta=on_delta,
        on_node_update=on_node_update,
    ):
        if event == "final_state":
            final_state = data
            continue
        yield event, data

    answer = final_state.get("final_response") or AI_DISABLED_MESSAGE

    yield "turn_end", {
        "answer": answer,
        "follow_up_message": final_state.get("follow_up_response"),
        "conversation_id": final_state.get("conversation_id"),
        "ai_enabled": final_state.get("ai_enabled", True),
        "should_end_session": bool(final_state.get("should_end_session")),
        "intent": final_state.get("intent"),
        "next_action": final_state.get("next_action"),
        "task_type": final_state.get("task_type"),
        "task_status": final_state.get("task_status"),
        "task_result": hydrate_task_result_for_response(
            final_state.get("task_result"),
            organization_id,
            session_id,
        ),
        "use_knowledge": final_state.get("use_knowledge", False),
        "decision_reason": final_state.get("decision_reason"),
        "applied_rules": final_state.get("applied_rules", []),
        "used_knowledge": final_state.get("used_knowledge", []),
        "knowledge_context": final_state.get("knowledge_context", []),
        "elapsed_ms": elapsed_ms_since(started_at),
    }
