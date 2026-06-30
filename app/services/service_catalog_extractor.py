import asyncio
import json
from typing import Any

from openai import OpenAI

from app.core.config import settings


client = OpenAI(api_key=settings.openai_api_key)


CATALOG_SYSTEM_PROMPT = """
너는 업장 지식 문서를 예약 가능한 서비스 카탈로그로 변환하는 AI다.

반드시 JSON만 반환한다.
마크다운, 설명문, 코드블록은 절대 반환하지 않는다.

추출 구조:
{
  "service_name": "서비스 대분류명",
  "description": "서비스 대분류 설명",
  "items": [
    {
      "name": "예약 가능한 세부 상품명",
      "description": "세부 상품 설명",
      "base_price": 0,
      "duration_minutes": 0,
      "options": [
        {
          "option_group": "옵션 그룹명",
          "option_value": "옵션 값",
          "description": "옵션 설명",
          "additional_price": 0,
          "additional_duration": 0
        }
      ]
    }
  ]
}

규칙:
- service_name은 문서 전체를 대표하는 대분류명이다.
  예: 홈 클리닝, 홈케어, 청소 서비스, 미용 서비스
- items에는 고객이 실제로 예약할 수 있는 상품을 넣는다.
  예: 이사 청소, 화장실 청소, 베란다 청소
- options에는 세부 상품 선택 후 추가로 고르는 값을 넣는다.
  예: 24평형, 34평형, 심한 곰팡이, 배수구 집중 청소
- 기본 가격은 base_price에 넣는다.
- 옵션 추가금은 additional_price에 넣는다.
- 기본 소요 시간은 duration_minutes에 넣는다.
- 옵션 추가 시간은 additional_duration에 넣는다.
- 가격이나 시간이 없으면 null이 아니라 0을 넣는다.
- 옵션이 없으면 빈 배열 []을 넣는다.
- 독립 예약 상품이 아닌 단순 설명, 주의사항, 정책은 items에 넣지 않는다.
"""


def _safe_json_loads(text: str) -> dict[str, Any]:
    content = str(text or "").strip()

    if content.startswith("```"):
        content = content.replace("```json", "").replace("```", "").strip()

    start = content.find("{")
    end = content.rfind("}")

    if start != -1 and end != -1 and end > start:
        content = content[start : end + 1]

    data = json.loads(content)

    if not isinstance(data, dict):
        raise ValueError("catalog extraction result must be a JSON object")

    return data


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_catalog(catalog: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
    service_name = str(catalog.get("service_name") or "").strip()
    if not service_name:
        service_name = fallback_title.replace(".txt", "").replace(".md", "").strip()

    description = str(catalog.get("description") or "").strip()

    normalized_items: list[dict[str, Any]] = []

    items = catalog.get("items") or []
    if not isinstance(items, list):
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue

        item_name = str(item.get("name") or "").strip()
        if not item_name:
            continue

        options = item.get("options") or []
        if not isinstance(options, list):
            options = []

        normalized_options: list[dict[str, Any]] = []

        for option in options:
            if not isinstance(option, dict):
                continue

            option_group = str(option.get("option_group") or "옵션").strip()
            option_value = str(option.get("option_value") or "").strip()

            if not option_value:
                continue

            normalized_options.append(
                {
                    "option_group": option_group,
                    "option_value": option_value,
                    "description": option.get("description"),
                    "additional_price": _to_int(option.get("additional_price"), 0),
                    "additional_duration": _to_int(option.get("additional_duration"), 0),
                }
            )

        normalized_items.append(
            {
                "name": item_name,
                "description": item.get("description"),
                "base_price": _to_int(
                    item.get("base_price", item.get("price")),
                    0,
                ),
                "duration_minutes": _to_int(item.get("duration_minutes"), 0),
                "options": normalized_options,
            }
        )

    return {
        "service_name": service_name,
        "description": description,
        "items": normalized_items,
    }


async def extract_service_catalog_from_text(
    *,
    title: str,
    text: str,
) -> dict[str, Any]:
    """
    지식 문서 텍스트에서 서비스 대분류, 세부 상품, 옵션을 추출한다.
    """

    user_prompt = f"""
문서 제목:
{title}

문서 내용:
{text}

위 문서에서 예약 가능한 서비스 카탈로그를 JSON으로 추출해줘.
"""

    response = await asyncio.to_thread(
        client.chat.completions.create,
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": CATALOG_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content or "{}"
    catalog = _safe_json_loads(content)

    return _normalize_catalog(catalog, fallback_title=title)