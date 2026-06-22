import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.graph.graph_runtime import build_initial_state, get_agent_graph, graph_config_for


router = APIRouter(tags=["Chat"])
AI_DISABLED_MESSAGE = "AI 자동응답이 꺼져 있어 관리자 응답을 기다립니다."

# LangGraph stream_mode="updates"가 알려주는 노드 완료 이벤트를 SSE trace 이벤트로 변환한다.
NODE_TRACE_LABELS = {
    "conversation": "대화 세션 확인 완료",
    "decision": "의도 분석 완료",
    "knowledge": "지식 검색 완료",
    "rule": "규칙 평가 완료",
    "response": "응답 생성 완료",
}


class ChatRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")
    session_id: str = Field(..., example="chat_test")
    message: str = Field(..., example="안녕하세요")
    folder_id: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    organization_id: str
    session_id: str

    # decision_node 결과
    intent: str
    next_action: str | None = None
    task_type: str | None = None
    use_knowledge: bool = False
    decision_reason: str | None = None

    # 최종 응답
    message: str

    # rules / knowledge 로그
    applied_rules: list[str]
    used_knowledge: list[dict]
    knowledge_context: list[dict]


def build_trace_detail(node_name: str, node_state: dict) -> tuple[str, list]:
    if node_name == "decision":
        detail = (
            f"intent={node_state.get('intent')} / "
            f"next_action={node_state.get('next_action')} / "
            f"task_type={node_state.get('task_type')}"
        )
        return detail, [node_state.get("decision_reason", "")]

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

    if node_name == "rule":
        rules = node_state.get("rules", [])
        items = [
            {
                "name": r.get("name", "unnamed"),
                "action_type": r.get("action_type", ""),
                "trigger_condition": r.get("trigger_condition", ""),
            }
            for r in rules
        ]
        return f"{len(rules)}개 규칙 적용", items

    return "", []


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    )
    config = graph_config_for(req.organization_id, req.session_id)

    final_state: dict = {}
    response_started = False

    try:
        async for mode, chunk in agent_graph.astream(
            initial_state,
            config=config,
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                if chunk.get("type") == "ai_response_delta":
                    if not response_started:
                        yield sse_event("response_start", {})
                        response_started = True

                    yield sse_event("delta", {"delta": chunk["delta"]})
                continue

            # mode == "updates": {node_name: partial_state}
            for node_name, node_state in chunk.items():
                final_state.update(node_state)

                label = NODE_TRACE_LABELS.get(node_name)
                if not label:
                    continue

                detail, items = build_trace_detail(node_name, node_state)
                yield sse_event(
                    "trace",
                    {
                        "step": node_name,
                        "status": "done",
                        "detail": detail or label,
                        "items": items,
                    },
                )

        if not final_state.get("ai_enabled", True):
            yield sse_event(
                "ai_disabled",
                {
                    "message": AI_DISABLED_MESSAGE,
                    "conversation_id": final_state.get("conversation_id"),
                },
            )
            yield sse_event("done", {})
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
                "message": final_state.get("final_response") or AI_DISABLED_MESSAGE,
                "applied_rules": final_state.get("applied_rules", []),
                "used_knowledge": final_state.get("used_knowledge", []),
                "knowledge_context": final_state.get("knowledge_context", []),
            },
        )
        yield sse_event("done", {})

    except Exception as e:
        yield sse_event("error", {"message": f"Agent response failed: {str(e)}"})
        yield sse_event("done", {})


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
            ),
            config=graph_config_for(req.organization_id, req.session_id),
        )

        return ChatResponse(
            organization_id=req.organization_id,
            session_id=req.session_id,

            # decision_node 결과
            intent=result.get("intent", "general"),
            next_action=result.get("next_action"),
            task_type=result.get("task_type"),
            use_knowledge=result.get("use_knowledge", False),
            decision_reason=result.get("decision_reason"),

            # 최종 응답
            message=result.get("final_response") or AI_DISABLED_MESSAGE,

            # rules / knowledge 로그
            applied_rules=result.get("applied_rules", []),
            used_knowledge=result.get("used_knowledge", []),
            knowledge_context=result.get("knowledge_context", []),
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Agent response failed: {str(e)}",
        )
