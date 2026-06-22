import json
from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.tasks.memory import TaskMemory
from app.tasks.types import ExecutorResult


client = OpenAI(api_key=settings.openai_api_key)


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

Instruction:
{instruction}

Output Schema:
{json.dumps(output_schema or {}, ensure_ascii=False, indent=2)}

Current Memory:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Current User Message:
{user_message or ""}
""".strip()


def execute_instruction_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
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

    prompt = _build_instruction_prompt(
        instruction=instruction,
        output_schema=output_schema,
        memory=memory.to_dict(),
        user_message=user_message,
    )

    try:
        response = client.chat.completions.create(
            model=getattr(settings, "openai_model", "gpt-4.1-mini"),
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
        )

        content = response.choices[0].message.content or "{}"
        parsed_result = _safe_json_loads(content)

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