from pydantic import BaseModel, Field


class RunTaskArgs(BaseModel):
    task_type: str = Field(
        description=(
            "reservation_create(새 예약) / reservation_lookup(예약 조회) / "
            "reservation_cancel(예약 취소) / reservation_update(예약 변경) 중 하나."
        )
    )


class RequestHandoffArgs(BaseModel):
    reason: str = Field(description="상담원 연결이 필요한 이유를 한국어로 짧게.")


class EndSessionArgs(BaseModel):
    farewell_message: str = Field(
        description=(
            "사용자에게 들려줄 짧고 따뜻한 작별 인사. "
            "\"통화를 종료하겠습니다\"처럼 시스템적으로 말하지 말고 "
            "\"네, 감사합니다. 좋은 하루 되세요\"처럼 자연스럽게 마무리한다."
        )
    )


# LangChain StructuredTool 등록용 메타데이터 (agent_node)
# search_knowledge는 코드 라우팅으로 직접 처리하므로 여기에 없음
AGENT_TOOL_SCHEMAS = [
    {
        "name": "run_task",
        "description": "예약 생성/조회/취소/변경을 시작하거나 이어간다.",
        "args_schema": RunTaskArgs,
    },
    {
        "name": "request_handoff",
        "description": "사람(상담원/직원) 연결을 요청한다.",
        "args_schema": RequestHandoffArgs,
    },
    {
        "name": "end_session",
        "description": "사용자가 상담·대화·통화를 끝내려고 할 때 호출한다.",
        "args_schema": EndSessionArgs,
    },
]

# OpenAI Realtime API function spec (realtime session config)
REALTIME_TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "run_task",
        "description": "예약 생성/조회/취소/변경을 시작하거나 이어간다.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": [
                        "reservation_create",
                        "reservation_lookup",
                        "reservation_cancel",
                        "reservation_update",
                    ],
                    "description": "실행할 태스크 종류.",
                },
            },
            "required": ["task_type"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "request_handoff",
        "description": "사람 상담원 연결을 요청한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "연결 이유를 짧게.",
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "end_session",
        "description": "사용자가 통화·상담을 끝내려 할 때 호출한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "farewell_message": {
                    "type": "string",
                    "description": "자연스러운 작별 인사 문장.",
                },
            },
            "required": ["farewell_message"],
            "additionalProperties": False,
        },
    },
]
