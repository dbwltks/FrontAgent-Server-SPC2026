import logging
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.graph.graph_runtime import build_initial_state, get_agent_graph, graph_config_for
from app.services.agent_stream import (
    AGENT_ERROR_MESSAGE,
    AI_DISABLED_MESSAGE,
    build_session_end_payload,
    build_trace_detail,  # noqa: F401 (재노출, 기존 테스트 호환용)
    elapsed_ms_since,
    sse_event,
    stream_agent_graph_events,
)


router = APIRouter(tags=["Chat"])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")
    session_id: str = Field(..., example="chat_test")
    message: str = Field(..., example="안녕하세요")
    folder_id: str | None = None
    stream: bool = True
    channel: Literal["web_chat", "web_call", "voice"] = "web_chat"


class ChatResponse(BaseModel):
    organization_id: str
    session_id: str
    conversation_id: str | None = None

    # decision_node 결과
    intent: str
    next_action: str | None = None
    task_type: str | None = None
    use_knowledge: bool = False
    decision_reason: str | None = None

    task_status: str | None = None
    task_result: dict | None = None

    # 최종 응답
    message: str

    # rules / knowledge 로그
    applied_rules: list[str]
    used_knowledge: list[dict]
    knowledge_context: list[dict]

    # 상담 종료 신호 (채팅·통화 공통)
    end_session: bool = False


async def stream_chat_response(req: ChatRequest):
    """
    그래프를 LangGraph 네이티브 스트리밍(astream)으로 실행하며
    토큰 delta와 노드별 진행상황(trace)을 SSE 이벤트로 흘려보낸다.
    """
    agent_graph = get_agent_graph()

    initial_state = build_initial_state(
        organization_id=req.organization_id,
        session_id=req.session_id,
        user_message=req.message,
        knowledge_folder_id=req.folder_id,
        channel=req.channel,
    )
    config = graph_config_for(req.organization_id, req.session_id)

    final_state: dict = {}
    started_at = time.perf_counter()

    try:
        async for event, data in stream_agent_graph_events(
            agent_graph=agent_graph,
            initial_state=initial_state,
            config=config,
            started_at=started_at,
        ):
            if event == "final_state":
                final_state = data
                continue
            yield sse_event(event, data)

        if not final_state.get("ai_enabled", True):
            yield sse_event(
                "ai_disabled",
                {
                    "message": AI_DISABLED_MESSAGE,
                    "conversation_id": final_state.get("conversation_id"),
                    "elapsed_ms": elapsed_ms_since(started_at),
                },
            )
            yield sse_event("done", {"elapsed_ms": elapsed_ms_since(started_at)})
            return

        yield sse_event(
            "result",
            {
                "organization_id": req.organization_id,
                "session_id": req.session_id,
                "intent": final_state.get("intent"),
                "next_action": final_state.get("next_action"),
                "task_type": final_state.get("task_type"),
                "use_knowledge": final_state.get("use_knowledge", False),
                "decision_reason": final_state.get("decision_reason"),
                "conversation_id": final_state.get("conversation_id"),
                "task_status": final_state.get("task_status"),
                "task_result": final_state.get("task_result"),
                "message": final_state.get("final_response") or AI_DISABLED_MESSAGE,
                "applied_rules": final_state.get("applied_rules", []),
                "used_knowledge": final_state.get("used_knowledge", []),
                "knowledge_context": final_state.get("knowledge_context", []),
                "should_end_session": bool(final_state.get("should_end_session")),
                "end_session": bool(final_state.get("should_end_session")),
                "end_call": bool(final_state.get("should_end_session")),
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )

        if final_state.get("should_end_session"):
            yield sse_event(
                "session_end",
                build_session_end_payload(
                    organization_id=req.organization_id,
                    session_id=req.session_id,
                    conversation_id=final_state.get("conversation_id"),
                    channel=req.channel,
                    started_at=started_at,
                ),
            )
            if req.channel in ("web_call", "voice"):
                yield sse_event(
                    "call_end",
                    build_session_end_payload(
                        organization_id=req.organization_id,
                        session_id=req.session_id,
                        conversation_id=final_state.get("conversation_id"),
                        channel=req.channel,
                        started_at=started_at,
                    ),
                )

        yield sse_event("done", {"elapsed_ms": elapsed_ms_since(started_at)})

    except Exception:
        logger.exception("streaming agent response failed")
        yield sse_event(
            "error",
            {
                "message": AGENT_ERROR_MESSAGE,
                "elapsed_ms": elapsed_ms_since(started_at),
            },
        )
        yield sse_event("done", {"elapsed_ms": elapsed_ms_since(started_at)})


@router.post("/chat")
async def chat(req: ChatRequest):
    """
    채팅(웹/전화/웹콜 등 모든 텍스트 기반 채널)이 공통으로 쓰는 단일 엔드포인트.

    stream=true(기본값)면 SSE로 토큰 delta + 노드별 진행상황(trace)을 실시간 전송한다.
    stream=false면 그래프를 끝까지 실행한 뒤 최종 결과만 한 번에 반환한다.
    음성 채널은 STT로 텍스트를 만들어 이 엔드포인트를 호출하고, 응답 텍스트를 TTS로 합성한다.
    """
    if req.stream:
        return StreamingResponse(
            stream_chat_response(req),
            media_type="text/event-stream",
        )

    try:
        agent_graph = get_agent_graph()

        result = await agent_graph.ainvoke(
            build_initial_state(
                organization_id=req.organization_id,
                session_id=req.session_id,
                user_message=req.message,
                knowledge_folder_id=req.folder_id,
                channel=req.channel,
            ),
            config=graph_config_for(req.organization_id, req.session_id),
        )

        return ChatResponse(
            organization_id=req.organization_id,
            session_id=req.session_id,
            conversation_id=result.get("conversation_id"),

            # decision_node 결과
            intent=result.get("intent", "general"),
            next_action=result.get("next_action"),
            task_type=result.get("task_type"),
            use_knowledge=result.get("use_knowledge", False),
            decision_reason=result.get("decision_reason"),

            task_status=result.get("task_status"),
            task_result=result.get("task_result"),

            # 최종 응답
            message=result.get("final_response") or AI_DISABLED_MESSAGE,

            # rules / knowledge 로그
            applied_rules=result.get("applied_rules", []),
            used_knowledge=result.get("used_knowledge", []),
            knowledge_context=result.get("knowledge_context", []),

            end_session=bool(result.get("should_end_session")),
        )

    except Exception:
        logger.exception("agent response failed")
        raise HTTPException(
            status_code=500,
            detail=AGENT_ERROR_MESSAGE,
        )
