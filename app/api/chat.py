import logging
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.graph.graph_runtime import build_initial_state, get_agent_graph, graph_config_for
from app.repositories.organization_repo import resolve_organization_id
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


class ChatMessageItem(BaseModel):
    type: Literal["answer", "follow_up"]
    message: str


class ChatRequest(BaseModel):
    organization_id: str = Field(..., example="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da")
    session_id: str = Field(..., example="chat_test")
    message: str = Field(..., example="안녕하세요")
    folder_id: str | None = None
    stream: bool = True
    channel: Literal["web_chat", "web_call", "voice"] = "web_chat"

    @field_validator("organization_id")
    @classmethod
    def _resolve_organization_id(cls, value: str) -> str:
        return resolve_organization_id(value)


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

    # task 결과
    task_status: str | None = None
    task_result: dict | None = None

    # 최종 응답
    # message는 기존 프론트/테스트 호환용으로 유지
    message: str

    # 신규: 지식 답변과 태스크 재질문을 분리해서 내려주기 위한 필드
    follow_up_message: str | None = None
    messages: list[ChatMessageItem] = Field(default_factory=list)

    # rules / knowledge 로그
    applied_rules: list[str]
    used_knowledge: list[dict]
    knowledge_context: list[dict]

    # 상담 종료 신호, 채팅·통화 공통
    end_session: bool = False


def build_response_messages(
    answer_message: str,
    follow_up_message: str | None,
) -> list[dict]:
    """
    SSE result 이벤트에서 사용할 messages payload를 만든다.

    answer:
        실제 AI 답변. 예: "베란다 청소는 ..."
    follow_up:
        진행 중이던 태스크 재질문. 예: "예약을 계속하려면 원하시는 서비스를 선택해 주세요."
    """
    messages = [
        {
            "type": "answer",
            "message": answer_message,
        }
    ]

    if follow_up_message:
        messages.append(
            {
                "type": "follow_up",
                "message": follow_up_message,
            }
        )

    return messages


def build_chat_response_messages(
    answer_message: str,
    follow_up_message: str | None,
) -> list[ChatMessageItem]:
    """
    stream=false 일반 JSON 응답에서 사용할 messages payload를 만든다.
    """
    messages = [
        ChatMessageItem(
            type="answer",
            message=answer_message,
        )
    ]

    if follow_up_message:
        messages.append(
            ChatMessageItem(
                type="follow_up",
                message=follow_up_message,
            )
        )

    return messages


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

        answer_message = final_state.get("final_response") or AI_DISABLED_MESSAGE
        follow_up_message = final_state.get("follow_up_response")

        messages = build_response_messages(
            answer_message=answer_message,
            follow_up_message=follow_up_message,
        )

        yield sse_event(
            "result",
            {
                "organization_id": req.organization_id,
                "session_id": req.session_id,
                "intent": final_state.get("intent") or "general",
                "next_action": final_state.get("next_action"),
                "task_type": final_state.get("task_type"),
                "use_knowledge": final_state.get("use_knowledge", False),
                "decision_reason": final_state.get("decision_reason"),
                "conversation_id": final_state.get("conversation_id"),
                "task_status": final_state.get("task_status"),
                "task_result": final_state.get("task_result"),

                # 기존 호환용:
                # 프론트가 아직 message만 읽고 있어도 지식 답변은 정상 표시됨
                "message": answer_message,

                # 신규:
                # 프론트는 messages를 보고 말풍선 2개로 렌더링하면 됨
                "follow_up_message": follow_up_message,
                "messages": messages,

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
            session_end_payload = build_session_end_payload(
                organization_id=req.organization_id,
                session_id=req.session_id,
                conversation_id=final_state.get("conversation_id"),
                channel=req.channel,
                started_at=started_at,
            )

            yield sse_event("session_end", session_end_payload)

            if req.channel in ("web_call", "voice"):
                yield sse_event("call_end", session_end_payload)

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

        answer_message = result.get("final_response") or AI_DISABLED_MESSAGE
        follow_up_message = result.get("follow_up_response")

        messages = build_chat_response_messages(
            answer_message=answer_message,
            follow_up_message=follow_up_message,
        )

        return ChatResponse(
            organization_id=req.organization_id,
            session_id=req.session_id,
            conversation_id=result.get("conversation_id"),

            # agent_node 결과 (tool 미호출 케이스 대비 .get(key, default)가 아니라
            # None도 함께 걸러낸다 - 키 자체는 항상 존재하므로 default만으로는 부족하다)
            intent=result.get("intent") or "general",
            next_action=result.get("next_action"),
            task_type=result.get("task_type"),
            use_knowledge=result.get("use_knowledge", False),
            decision_reason=result.get("decision_reason"),

            # task 결과
            task_status=result.get("task_status"),
            task_result=result.get("task_result"),

            # 기존 호환용 최종 응답
            message=answer_message,

            # 신규: 말풍선 분리 응답
            follow_up_message=follow_up_message,
            messages=messages,

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