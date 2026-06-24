from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.tasks.memory import TaskMemory


def generate_task_question(
    *,
    node: dict[str, Any],
    memory: TaskMemory,
    fallback_message: str,
) -> str:
    config = node.get("config") or {}

    variable_name = config.get("variable_name")
    slot_description = (
        config.get("slot_description")
        or config.get("description")
        or variable_name
        or fallback_message
    )

    variables = memory.to_dict()

    prompt = f"""
당신은 예약 챗봇의 상담원입니다.

현재 태스크 단계에서 고객에게 필요한 정보 하나를 자연스럽게 물어봐야 합니다.

[현재 노드]
- node_key: {node.get("node_key")}
- node_type: {node.get("node_type")}
- 기본 질문: {fallback_message}
- 저장할 변수명: {variable_name}
- 받아야 할 정보 설명: {slot_description}

[현재까지 수집된 정보]
{variables}

[규칙]
- 한국어로 답변하세요.
- 이번 단계에서 필요한 정보 하나만 물어보세요.
- 너무 길게 설명하지 마세요.
- 기본 질문 문장을 그대로 복사하지 마세요.
- 같은 의미를 유지하되 표현을 다르게 바꾸세요.
- 매번 자연스럽게 다른 표현을 사용하세요.
- 예약이 확정되었다고 말하지 마세요.
- 모르는 정보를 지어내지 마세요.
- 최종 출력은 고객에게 보여줄 문장 하나만 작성하세요.
"""

    try:
        client = OpenAI(api_key=settings.openai_api_key)

        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 친절하고 자연스러운 예약 상담 챗봇입니다.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.8,
            max_tokens=120,
        )

        message = response.choices[0].message.content

        if not message:
            return fallback_message

        return message.strip()

    except Exception as e:
        print("TASK_RESPONSE_GENERATOR_FAILED:", repr(e))
        return fallback_message