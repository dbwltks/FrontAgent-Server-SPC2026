import hashlib
import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.core.config import settings


router = APIRouter(prefix="/voice", tags=["Voice"])
logger = logging.getLogger(__name__)
OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
REALTIME_ERROR_MESSAGE = "Realtime voice connection failed"


def build_realtime_session_config() -> dict:
    return {
        "type": "realtime",
        "model": settings.openai_realtime_model,
        "instructions": (
            "너는 Front Agent의 음성 입출력 인터페이스다. "
            "사용자가 말할 때마다 query_agent 함수를 정확히 한 번 호출하고, "
            "message에는 사용자의 발화를 한국어 텍스트로 전달한다. "
            "함수 결과를 받기 전에는 자체 지식으로 답하지 않는다. "
            "함수 결과를 받은 뒤에는 내용을 추가하거나 바꾸지 말고 자연스럽게 읽는다."
        ),
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "voice": settings.openai_realtime_voice,
            },
        },
        "tools": [
            {
                "type": "function",
                "name": "query_agent",
                "description": "사용자 발화를 Front Agent LangGraph에 전달해 최종 답변을 받는다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "사용자가 방금 말한 내용을 빠짐없이 정리한 한국어 문장",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
            },
        ],
        "tool_choice": "required",
    }


@router.post("/realtime")
async def create_realtime_call(
    request: Request,
    organization_id: str = Query(...),
    session_id: str = Query(...),
):
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/sdp":
        raise HTTPException(status_code=415, detail="Content-Type must be application/sdp")

    sdp = (await request.body()).decode("utf-8")

    if not sdp.strip():
        raise HTTPException(status_code=400, detail="SDP offer is required")

    safety_identifier = hashlib.sha256(
        f"{organization_id}:{session_id}".encode("utf-8")
    ).hexdigest()

    files = {
        "sdp": (None, sdp, "application/sdp"),
        "session": (
            None,
            json.dumps(build_realtime_session_config(), ensure_ascii=False),
            "application/json",
        ),
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "OpenAI-Safety-Identifier": safety_identifier,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            openai_response = await client.post(
                OPENAI_REALTIME_CALLS_URL,
                headers=headers,
                files=files,
            )
    except httpx.HTTPError:
        logger.exception("OpenAI Realtime call creation failed")
        raise HTTPException(status_code=502, detail=REALTIME_ERROR_MESSAGE)

    if not openai_response.is_success:
        logger.error(
            "OpenAI Realtime rejected call: status=%s body=%s",
            openai_response.status_code,
            openai_response.text[:500],
        )
        raise HTTPException(status_code=502, detail=REALTIME_ERROR_MESSAGE)

    return Response(
        content=openai_response.text,
        media_type="application/sdp",
    )
