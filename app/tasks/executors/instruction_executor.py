import json
import re
from datetime import datetime
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.tasks.edge_evaluator import evaluate_condition_expression, get_value_by_path
from app.tasks.memory import TaskMemory
from app.tasks.service_selection import (
    try_fast_path_ask_cancel_number_instruction,
    try_fast_path_ask_service_instruction,
)
from app.tasks.types import ExecutorResult


client = AsyncOpenAI(api_key=settings.openai_api_key)

_WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def _current_datetime_context() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M')} ({_WEEKDAY_KO[now.weekday()]})"


PROMPTDATA_PATTERN = re.compile(
    r'<promptdata\s+[^>]*type="(?P<type>[^"]+)"[^>]*subtype="(?P<subtype>[^"]+)"[^>]*identifier="(?P<identifier>[^"]+)"[^>]*>.*?</promptdata>',
    re.DOTALL,
)


def _extract_promptdata_variables(instruction: str) -> dict[str, list[str]]:
    variables = {"read": [], "update": []}

    for match in PROMPTDATA_PATTERN.finditer(instruction or ""):
        prompt_type = match.group("type")
        identifier = match.group("identifier")

        if prompt_type == "read-variable":
            variables["read"].append(identifier)
        elif prompt_type == "update-variable":
            variables["update"].append(identifier)

    return {
        key: list(dict.fromkeys(value))
        for key, value in variables.items()
    }


def _is_conversation_instruction(instruction: str) -> bool:
    return "<promptdata" in (instruction or "")


def _safe_json_loads(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {"result": data}
    except json.JSONDecodeError:
        return {
            "raw_result": text,
        }


def _build_instruction_prompt(
    instruction: str,
    output_schema: dict[str, Any] | None,
    memory: dict[str, Any],
    user_message: str | None,
) -> str:
    return f"""
너는 Front Agent의 Dynamic Task Runner 안에서 실행되는 Instruction Node다.

역할:
- 사용자의 메시지와 현재 memory를 보고 instruction을 수행한다.
- 반드시 JSON 형식으로만 응답한다.
- 설명 문장, 마크다운, 코드블록은 절대 포함하지 않는다.
- 값이 없으면 null로 둔다.
- "내일", "모레", "다음 주 토요일"처럼 상대적인 날짜/시간 표현이 나오면 아래 현재 시각을 기준으로 절대 날짜/시간으로 변환한다.

Current Datetime:
{_current_datetime_context()}

Instruction:
{instruction}

Output Schema:
{json.dumps(output_schema or {}, ensure_ascii=False, indent=2)}

Current Memory:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Current User Message:
{user_message or ""}
""".strip()


def _build_conversation_instruction_prompt(
    instruction: str,
    memory: dict[str, Any],
    user_message: str | None,
) -> str:
    variables = _extract_promptdata_variables(instruction)
    read_values = {
        identifier: get_value_by_path(memory, identifier)
        for identifier in variables["read"]
    }

    return f"""
너는 Front Agent의 Dynamic Task Runner 안에서 실행되는 대화형 AI 에이전트 노드다.

목표:
- 아래 Instruction의 대화 흐름을 따른다.
- Current Memory를 읽고, 고객 메시지를 해석한다.
- <promptdata type="update-variable" ... identifier="...">로 지정된 변수만 업데이트한다.
- 종료 조건이 충족되면 is_complete=true를 반환한다.
- 종료 조건이 충족되지 않으면 고객에게 다음 질문을 message로 반환한다.
- 반드시 JSON만 반환한다. 마크다운, 코드블록, 설명 문장은 금지한다.

응답 JSON 형식:
{{
  "message": string | null,
  "memory_updates": object,
  "is_complete": boolean
}}

규칙:
- memory_updates에는 실제로 새로 알게 되었거나 수정할 값만 넣는다.
- 업데이트 가능한 변수: {json.dumps(variables["update"], ensure_ascii=False)}
- 읽기 전용 변수와 현재 값: {json.dumps(read_values, ensure_ascii=False)}
- 고객 답변이 모호하면 값을 추측하지 말고 message로 확인 질문을 한다.
- "내일", "모레", "다음 주 토요일", "이번 주말"처럼 상대적인 날짜/시간 표현은 모호한 것이 아니라, 아래 현재 시각을 기준으로 절대 날짜/시간(YYYY-MM-DD, HH:MM)으로 변환해서 저장한다.
- Instruction에 정의된 슬롯 중, 고객 한 메시지에서 동시에 추출 가능한 값은 memory_updates에 함께 넣는다(예: "7월2일 14시" → reservation_date + reservation_time).
- Current Memory에 이미 채워진 슬롯은 message에서 다시 묻지 않는다. message는 아직 비어 있는 슬롯만 질문한다.
- 이미 memory에 있는 값은 유지한다.
- 종료 조건이 충족되면 message는 null로 둔다.

Current Datetime:
{_current_datetime_context()}

Instruction:
{instruction}

Current Memory:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Current User Message:
{user_message or ""}
""".strip()


async def execute_instruction_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
    organization_id: str | None = None,
) -> ExecutorResult:
    config = node.get("config") or {}

    instruction = config.get("instruction")
    output_schema = config.get("output_schema") or {}
    save_to_memory = config.get("save_to_memory", True)
    save_as = config.get("save_as")

    if not instruction:
        return ExecutorResult(
            status="failed",
            message="Instruction Node에 instruction이 설정되어 있지 않습니다.",
            next_behavior="fail",
            error={
                "code": "INSTRUCTION_MISSING",
                "message": "config.instruction is required.",
            },
        )

    fast_path = try_fast_path_ask_service_instruction(
        node=node,
        memory=memory,
        user_message=user_message,
        organization_id=organization_id,
    )
    if fast_path is not None:
        return fast_path

    fast_path = try_fast_path_ask_cancel_number_instruction(
        node=node,
        memory=memory,
        user_message=user_message,
    )
    if fast_path is not None:
        return fast_path

    is_conversation_instruction = _is_conversation_instruction(instruction)
    prompt = (
        _build_conversation_instruction_prompt(
            instruction=instruction,
            memory=memory.to_dict(),
            user_message=user_message,
        )
        if is_conversation_instruction
        else _build_instruction_prompt(
            instruction=instruction,
            output_schema=output_schema,
            memory=memory.to_dict(),
            user_message=user_message,
        )
    )

    try:
        response = await client.chat.completions.create(
            model=getattr(settings, "task_instruction_model", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": "너는 태스크 실행 중 필요한 값을 추출하거나 판단하는 JSON 전용 실행기다.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0,
            # JSON 강제 모드: 모델이 서두/검증 토큰 없이 곧바로 JSON을 생성해
            # 응답 시간을 줄인다. 프롬프트만으로 "JSON만 응답"을 지시하던 기존
            # 방식보다 안정적으로 빠르다.
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or "{}"
        parsed_result = _safe_json_loads(content)

        if is_conversation_instruction:
            allowed_updates = set(_extract_promptdata_variables(instruction)["update"])
            raw_updates = parsed_result.get("memory_updates") or {}
            memory_updates = {
                key: value
                for key, value in raw_updates.items()
                if key in allowed_updates
            }
            is_complete = parsed_result.get("is_complete") is True
            message = parsed_result.get("message")

            branch_condition = config.get("branch_condition")
            if branch_condition and branch_condition.strip():
                merged_memory = {**memory.to_dict(), **memory_updates}
                if evaluate_condition_expression(branch_condition, merged_memory):
                    is_complete = True
                    message = None

            return ExecutorResult(
                status="success",
                message=message if not is_complete else None,
                memory_updates=memory_updates if save_to_memory else {},
                next_behavior="evaluate_edges" if is_complete else "wait_user",
            )

        memory_updates: dict[str, Any] = {}

        if save_to_memory:
            if save_as:
                memory_updates[save_as] = parsed_result
            else:
                memory_updates.update(parsed_result)

        return ExecutorResult(
            status="success",
            message=None,
            memory_updates=memory_updates,
            next_behavior="evaluate_edges",
        )

    except Exception as error:
        return ExecutorResult(
            status="failed",
            message="Instruction Node 실행 중 오류가 발생했습니다.",
            next_behavior="fail",
            error={
                "code": "INSTRUCTION_EXECUTION_FAILED",
                "message": str(error),
            },
        )