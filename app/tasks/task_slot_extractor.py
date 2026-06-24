import json
import re
from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.tasks.memory import TaskMemory


def _fallback_extract(variable_name: str | None, user_message: str | None) -> Any:
    """
    AI 추출 실패 시 최소한의 정리만 수행한다.
    """
    if not user_message:
        return user_message

    text = user_message.strip()

    if variable_name in {"customer_name", "name", "user_name"}:
        prefixes = [
            "저는 ",
            "제 이름은 ",
            "이름은 ",
            "예약자는 ",
            "예약자명은 ",
        ]

        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        suffixes = [
            "입니다",
            "이에요",
            "예요",
            "이요",
            "요",
            "입니다.",
            "이에요.",
            "예요.",
        ]

        for suffix in suffixes:
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()

        return text

    return text


def extract_slot_value(
    *,
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None,
) -> Any:
    """
    Ask Node에서 사용자가 입력한 문장에서 실제 변수에 저장할 값만 추출한다.
    예:
    - "이상욱입니다" -> "이상욱"
    - "두 명이요" -> 2
    - "내일 오후 3시요" -> "내일 오후 3시"
    """

    config = node.get("config") or {}

    variable_name = config.get("variable_name")
    slot_description = (
        config.get("slot_description")
        or config.get("description")
        or config.get("question")
        or node.get("label")
        or variable_name
    )

    fallback_value = _fallback_extract(variable_name, user_message)

    if not user_message:
        return fallback_value

    try:
        variables = memory.to_dict()
    except Exception:
        variables = {}

    prompt = f"""
당신은 예약 태스크에서 사용자의 답변을 구조화된 변수 값으로 추출하는 역할입니다.

[현재 노드]
- node_key: {node.get("node_key")}
- node_type: {node.get("node_type")}
- 저장할 변수명: {variable_name}
- 받아야 하는 정보 설명: {slot_description}

[현재까지 수집된 정보]
{variables}

[사용자 입력]
{user_message}

[규칙]
- 사용자의 답변에서 저장할 값만 추출하세요.
- 조사, 존댓말, 불필요한 문장은 제거하세요.
- 이름이면 이름만 추출하세요. 예: "이상욱입니다" -> "이상욱"
- 인원이면 숫자로 추출하세요. 예: "두 명이요" -> 2
- 날짜/시간이면 사용자가 말한 핵심 표현만 추출하세요.
- 확실하지 않으면 원문에서 가장 핵심 값만 반환하세요.
- 반드시 JSON만 출력하세요.

[출력 형식]
{{
  "value": 추출한 값
}}
"""

    try:
        client = OpenAI(api_key=settings.openai_api_key)

        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 사용자의 자연어 답변에서 필요한 값만 추출하는 함수입니다.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0,
            max_tokens=120,
        )

        content = response.choices[0].message.content

        if not content:
            return fallback_value

        content = content.strip()

        # 혹시 코드블록으로 감싸져 나와도 JSON 부분만 추출
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            content = match.group(0)

        parsed = json.loads(content)

        value = parsed.get("value")

        if value is None or value == "":
            return fallback_value

        return value

    except Exception as e:
        print("TASK_SLOT_EXTRACTOR_FAILED:", repr(e))
        return fallback_value