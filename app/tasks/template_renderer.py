import re
from typing import Any

from app.tasks.edge_evaluator import get_value_by_path
from app.tasks.memory import TaskMemory


TEMPLATE_PATTERN = re.compile(
    r"{{\s*(memory\.[a-zA-Z0-9_.]+)(?:\s*\|\s*(?:default\s*:\s*)?([^}]+?))?\s*}}"
)

FULL_TEMPLATE_PATTERN = re.compile(
    r"^{{\s*(memory\.[a-zA-Z0-9_.]+)(?:\s*\|\s*(?:default\s*:\s*)?([^}]+?))?\s*}}$"
)


def _clean_default_value(default_value: str | None) -> str:
    if default_value is None:
        return ""

    cleaned = default_value.strip()

    if (
        (cleaned.startswith('"') and cleaned.endswith('"'))
        or (cleaned.startswith("'") and cleaned.endswith("'"))
    ):
        return cleaned[1:-1]

    return cleaned


def _resolve_template_match(
    match: re.Match,
    memory_data: dict[str, Any],
) -> Any:
    memory_path = match.group(1)
    default_value = _clean_default_value(match.group(2))

    resolved_value = get_value_by_path(
        data=memory_data,
        path=memory_path,
    )

    if resolved_value is None or resolved_value == "":
        return default_value

    return resolved_value


def render_text_template(
    text: str | None,
    memory: TaskMemory,
) -> str:
    """
    문자열 안의 {{memory.xxx}} 값을 치환한다.

    예:
    "{{memory.customer_name}}님 안녕하세요"
    "{{memory.customer.name}}님 안녕하세요"
    "{{memory.customer_name|고객}}님 안녕하세요"
    "{{memory.customer_name|default:고객}}님 안녕하세요"
    """

    if not text:
        return ""

    memory_data = memory.to_dict()

    def replace(match: re.Match) -> str:
        resolved_value = _resolve_template_match(
            match=match,
            memory_data=memory_data,
        )

        if resolved_value is None:
            return ""

        return str(resolved_value)

    return TEMPLATE_PATTERN.sub(replace, text)


def resolve_template_value(
    value: Any,
    memory: TaskMemory,
) -> Any:
    """
    Function Node params 같은 dict/list 구조 안의 template 값을 실제 값으로 바꾼다.

    전체가 template이면 원래 타입을 유지한다.
    예:
    "{{memory.party_size}}" -> 2

    문자열 일부에 template이 포함되면 문자열로 치환한다.
    예:
    "예약 인원: {{memory.party_size}}명" -> "예약 인원: 2명"
    """

    if isinstance(value, dict):
        return {
            key: resolve_template_value(child_value, memory)
            for key, child_value in value.items()
        }

    if isinstance(value, list):
        return [
            resolve_template_value(item, memory)
            for item in value
        ]

    if not isinstance(value, str):
        return value

    stripped_value = value.strip()
    full_match = FULL_TEMPLATE_PATTERN.match(stripped_value)

    if full_match:
        return _resolve_template_match(
            match=full_match,
            memory_data=memory.to_dict(),
        )

    return render_text_template(value, memory)