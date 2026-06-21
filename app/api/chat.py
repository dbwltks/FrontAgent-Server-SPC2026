from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.graph.graph import agent_graph


router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")
    session_id: str = Field(..., example="chat_test")
    message: str = Field(..., example="안녕하세요")
    folder_id: str | None = None
    


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
    message: str | None
    session_state: dict

    # rules / knowledge 로그
    applied_rules: list[str]
    used_knowledge: list[dict]
    knowledge_context: list[dict]


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        result = await agent_graph.ainvoke(
            {
                "organization_id": req.organization_id,
                "session_id": req.session_id,
                "user_message": req.message,
                "conversation_id": None,
                "ai_enabled": True,
                "session_state": {},
                "conversation_history": [],

                # decision_node 결과
                "intent": None,
                "next_action": None,
                "task_type": None,
                "use_knowledge": False,
                "decision_reason": None,
                "task_result": None,

                # 기존 should_use_knowledge_node와의 호환용
                "should_use_knowledge": False,

                # rules
                "rules": [],
                "rule_instructions": "",
                "applied_rules": [],

                # knowledge
                "knowledge_context": [],
                "used_knowledge": [],

                # final response
                "final_response": None,
            }
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
            message=result.get("final_response"),
            session_state=result.get("session_state", {}),

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