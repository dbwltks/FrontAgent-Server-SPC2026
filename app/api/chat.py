from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.graph.graph import agent_graph


router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")
    session_id: str = Field(..., example="chat_test")
    message: str = Field(..., example="안녕하세요")


class ChatResponse(BaseModel):
    organization_id: str
    session_id: str
    intent: str
    message: str
    session_state: dict
    applied_rules: list[str]
    used_knowledge: list[dict]
    knowledge_context: list[dict]


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        result = agent_graph.invoke({
            "organization_id": req.organization_id,
            "session_id": req.session_id,
            "user_message": req.message,
            "conversation_id": None,
            "session_state": {},
            "intent": None,
            "rules": [],
            "applied_rules": [],
            "knowledge_context": [],
            "used_knowledge": [],
            "final_response": None,
        })

        return ChatResponse(
            organization_id=req.organization_id,
            session_id=req.session_id,
            intent=result["intent"],
            message=result["final_response"],
            session_state=result["session_state"],
            applied_rules=result.get("applied_rules", []),
            used_knowledge=result.get("used_knowledge", []),
            knowledge_context=result.get("knowledge_context", []),
)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Agent response failed: {str(e)}"
        )