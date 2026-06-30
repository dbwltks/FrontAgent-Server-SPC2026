import json
import time
from typing import Any, AsyncIterator, Awaitable, Callable

AI_DISABLED_MESSAGE = "AI 자동응답이 꺼져 있어 관리자 응답을 기다립니다."
AGENT_ERROR_MESSAGE = "Agent response failed"

# LangGraph stream_mode="updates"가 알려주는 노드 완료 이벤트를 SSE trace 이벤트로 변환한다.
NODE_TRACE_LABELS = {
    "prepare": "대화 세션/의도 분석 완료",
    "task_router": "태스크 라우팅 완료",
    "task": "태스크 실행 완료",
    "knowledge": "지식 검색 완료",
    "response": "응답 생성 완료",
    "finalize": "마무리 처리 완료",
}


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def elapsed_ms_since(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def build_session_end_payload(
    *,
    organization_id: str,
    session_id: str,
    conversation_id: str | None,
    channel: str,
    started_at: float,
    reason: str = "user_requested",
) -> dict:
    return {
        "reason": reason,
        "organization_id": organization_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "channel": channel,
        "elapsed_ms": elapsed_ms_since(started_at),
    }


def build_call_end_payload(
    *,
    organization_id: str,
    session_id: str,
    conversation_id: str | None,
    started_at: float,
    reason: str = "user_requested",
    channel: str = "web_call",
) -> dict:
    """web_call/realtime 호환용. session_end와 동일 payload에 channel을 포함한다."""
    return build_session_end_payload(
        organization_id=organization_id,
        session_id=session_id,
        conversation_id=conversation_id,
        channel=channel,
        started_at=started_at,
        reason=reason,
    )


def build_trace_detail(node_name: str, node_state: dict) -> tuple[str, list]:
    if node_name == "prepare":
        detail = (
            f"intent={node_state.get('intent')} / "
            f"next_action={node_state.get('next_action')} / "
            f"task_type={node_state.get('task_type')}"
        )
        return detail, [node_state.get("decision_reason", "")]

    if node_name == "task":
        task_result = node_state.get("task_result") or {}
        task_trace = task_result.get("trace") or []

        return (
            f"status={task_result.get('status')} / "
            f"current_node={task_result.get('current_node_key')} / "
            f"steps={len(task_trace)}",
            task_trace,
        )

    if node_name == "knowledge":
        groups = node_state.get("knowledge_context_groups", [])
        sources = [k.get("source_title", "") for k in node_state.get("used_knowledge", [])]
        items = [
            {
                "query": g.get("query"),
                "chunks": [
                    {
                        "source_title": c.get("source_title"),
                        "similarity": c.get("similarity"),
                    }
                    for c in g.get("chunks", [])
                ],
            }
            for g in groups
        ]
        return f"{len(node_state.get('knowledge_queries', []))}개 질문 / {len(sources)}개 문서 참조", items

    return "", []


async def stream_agent_graph_events(
    *,
    agent_graph,
    initial_state: dict,
    config: dict,
    started_at: float,
    on_delta: Callable[[str], Awaitable[None] | None] | None = None,
    on_node_update: Callable[[str, dict], Awaitable[None] | None] | None = None,
) -> AsyncIterator[tuple[str, dict]]:
    """
    LangGraph astream(stream_mode=["custom", "updates"])을 순회하며
    chat/voice 스트림이 공통으로 쓰는 SSE 이벤트를 (event, data) 튜플로 만들어낸다.

    delta/노드 업데이트가 생길 때마다 호출자가 TTS 스케줄링 등
    채널별 부가 동작을 끼워넣을 수 있도록 on_delta/on_node_update 훅을 받는다.

    final_state는 마지막에 yield하는 ("final_state", final_state) 항목으로 전달한다.
    """
    final_state: dict = {}
    response_started = False

    async for mode, chunk in agent_graph.astream(
        initial_state,
        config=config,
        stream_mode=["custom", "updates"],
    ):
        if mode == "custom":
            chunk_type = chunk.get("type")

            if chunk_type == "ai_response_delta":
                if not response_started:
                    yield "response_start", {
                        "elapsed_ms": elapsed_ms_since(started_at)
                    }
                    response_started = True

                delta = str(chunk.get("delta") or "")

                yield "delta", {
                    "delta": delta,
                    "elapsed_ms": elapsed_ms_since(started_at),
                }

                if on_delta:
                    result = on_delta(delta)
                    if result is not None:
                        await result

            elif chunk_type == "ai_follow_up_message":
                yield "follow_up_message", {
                    "message": str(chunk.get("message") or ""),
                    "elapsed_ms": elapsed_ms_since(started_at),
                }

            elif chunk_type == "knowledge_start":
                yield "knowledge_start", {
                    "queries": chunk.get("queries", []),
                    "elapsed_ms": elapsed_ms_since(started_at),
                }

            elif chunk_type == "task_step":
                step = chunk.get("step") or {}
                label = step.get("node_label") or step.get("node_key") or "태스크 단계"

                yield "trace", {
                    "step": "task",
                    "status": "step",
                    "detail": (
                        f"{label} / "
                        f"type={step.get('node_type')} / "
                        f"next={step.get('next_behavior')}"
                    ),
                    "items": [step],
                    "elapsed_ms": elapsed_ms_since(started_at),
                }

            # 중요:
            # custom 이벤트는 여기서 끝내야 함.
            # 아래 updates 처리로 내려가면 잘못된 chunk를 node_state처럼 해석할 수 있음.
            continue

        # mode == "updates": {node_name: partial_state}
        for node_name, node_state in chunk.items():
            if not isinstance(node_state, dict):
                continue

            final_state.update(node_state)

            if on_node_update:
                result = on_node_update(node_name, node_state)
                if result is not None:
                    await result

            label = NODE_TRACE_LABELS.get(node_name)
            if not label:
                continue

            detail, items = build_trace_detail(node_name, node_state)

            yield "trace", {
                "step": node_name,
                "status": "done",
                "detail": detail or label,
                "items": items,
                "elapsed_ms": elapsed_ms_since(started_at),
            }

    yield "final_state", final_state
