import time
from datetime import datetime, timezone

from app.core.db import supabase

# 모든 조직에 기본 제공되는 고정 규칙 3개. 삭제는 불가능하고, 조직별로
# 수정하거나 끄고 켤 수 있다. 본문(name/instruction/is_active)은 여기 코드가
# 기준값이고, 조직이 수정하면 builtin_rule_overrides에 오버라이드만 저장한다.
# "기본값으로 복구"는 그 오버라이드 row를 지우는 것과 같다.
BUILTIN_RULES: list[dict] = [
    {
        "builtin_key": "tone_polite",
        "name": "기본 CX 톤앤매너",
        "instruction": (
            "- 친절하고 편안한 말투를 쓰되, 실제 상담원처럼 자연스럽게 대답한다.\n"
            "- 고객이 질문한 내용에만 간결하게 답하고, 묻지 않은 정보는 덧붙이지 않는다.\n"
            "- 상투적인 인사말 반복 없이 바로 핵심부터 답한다."
        ),
        "is_active": True,
    },
    {
        "builtin_key": "no_guessing",
        "name": "모르면 지어내지 않기",
        "instruction": (
            "- 확실하지 않은 내용은 추측하거나 만들어내지 않는다.\n"
            "- 정보가 없으면 모른다고 솔직히 말하고, 담당자 확인이 필요하다고 안내한다.\n"
            "- 확인 후 다시 안내드릴 시점이나 방법을 함께 제시한다."
        ),
        "is_active": True,
    },
    {
        "builtin_key": "stay_on_topic",
        "name": "상담 범위 유지",
        "instruction": (
            "- 회사 서비스와 무관한 질문에는 정중히 답변을 사양한다.\n"
            "- 답변을 거절할 때도 단호하지 않게, 상담 주제로 자연스럽게 다시 안내한다.\n"
            "- 사적인 의견이나 추천이 필요한 질문에는 답하지 않는다."
        ),
        "is_active": True,
    },
]

_BUILTIN_RULES_BY_KEY = {rule["builtin_key"]: rule for rule in BUILTIN_RULES}

# 활성 규칙은 organization 설정(app/providers/langchain_provider.py의
# _organization_cache)과 같은 성격의 작은 정적 메타데이터다. 워커 프로세스
# 안에서만 유효한 인메모리 TTL 캐시로 충분하고, Redis 왕복을 새로 추가할
# 만큼 무겁거나 여러 워커 간 즉시 동기화가 필요한 데이터가 아니다.
_ACTIVE_RULES_CACHE_TTL_SECONDS = 60
_active_rules_cache: dict[str, tuple[float, list[dict]]] = {}


def _apply_builtin_override(builtin: dict, override: dict | None) -> dict:
    """코드 기본값에 오버라이드 row를 합성해 하나의 규칙 dict로 만든다."""
    merged = {
        "id": f"builtin:{builtin['builtin_key']}",
        "organization_id": override.get("organization_id") if override else None,
        "name": builtin["name"],
        "instruction": builtin["instruction"],
        "is_active": builtin["is_active"],
        "is_builtin": True,
        "builtin_key": builtin["builtin_key"],
        "created_at": None,
        "updated_at": None,
    }

    if override:
        if override.get("name") is not None:
            merged["name"] = override["name"]
        if override.get("instruction") is not None:
            merged["instruction"] = override["instruction"]
        if override.get("is_active") is not None:
            merged["is_active"] = override["is_active"]
        merged["updated_at"] = override.get("updated_at")

    return merged


def list_builtin_rules(organization_id: str) -> list[dict]:
    """빌트인 규칙 3개에 조직별 오버라이드를 합성해 반환한다."""

    result = (
        supabase.table("builtin_rule_overrides")
        .select("*")
        .eq("organization_id", organization_id)
        .execute()
    )

    overrides_by_key = {row["builtin_key"]: row for row in (result.data or [])}

    return [
        _apply_builtin_override(builtin, overrides_by_key.get(builtin["builtin_key"]))
        for builtin in BUILTIN_RULES
    ]


def update_builtin_rule(
    organization_id: str,
    builtin_key: str,
    data: dict,
) -> dict | None:
    """
    빌트인 규칙을 수정한다. 실제로는 오버라이드 row를 upsert하는 것이다.

    수정 가능한 값: name, instruction, is_active
    """

    if builtin_key not in _BUILTIN_RULES_BY_KEY:
        return None

    allowed_fields = {"name", "instruction", "is_active"}
    update_data = {key: value for key, value in data.items() if key in allowed_fields}

    if not update_data:
        return None

    payload = {
        "organization_id": organization_id,
        "builtin_key": builtin_key,
        "updated_at": utc_now_iso(),
        **update_data,
    }

    result = (
        supabase.table("builtin_rule_overrides")
        .upsert(payload, on_conflict="organization_id,builtin_key")
        .execute()
    )

    if not result.data:
        return None

    _invalidate_active_rules_cache(organization_id)
    return _apply_builtin_override(_BUILTIN_RULES_BY_KEY[builtin_key], result.data[0])


def reset_builtin_rule(organization_id: str, builtin_key: str) -> dict | None:
    """오버라이드를 지워 빌트인 규칙을 코드 기본값으로 되돌린다."""

    if builtin_key not in _BUILTIN_RULES_BY_KEY:
        return None

    (
        supabase.table("builtin_rule_overrides")
        .delete()
        .eq("organization_id", organization_id)
        .eq("builtin_key", builtin_key)
        .execute()
    )

    _invalidate_active_rules_cache(organization_id)
    return _apply_builtin_override(_BUILTIN_RULES_BY_KEY[builtin_key], None)


def _invalidate_active_rules_cache(organization_id: str) -> None:
    _active_rules_cache.pop(organization_id, None)


def utc_now_iso() -> str:
    """
    Supabase timestamp 컬럼에 넣기 좋은 UTC ISO 문자열을 만든다.
    """
    return datetime.now(timezone.utc).isoformat()


def create_rule(data: dict) -> dict | None:
    """
    규칙을 생성한다.

    이번 rules 구조에서는 필터, 트리거, 액션을 저장하지 않는다.
    오직 규칙 이름과 지시문만 저장한다.
    """

    payload = {
        "organization_id": data["organization_id"],
        "name": data["name"],
        "instruction": data["instruction"],
        "is_active": data.get("is_active", True),
    }

    result = (
        supabase.table("rules")
        .insert(payload)
        .execute()
    )

    if not result.data:
        return None

    _invalidate_active_rules_cache(data["organization_id"])
    return result.data[0]


def list_rules(organization_id: str) -> list[dict]:
    """
    특정 조직의 전체 규칙 목록을 조회한다.
    관리자 화면에서 규칙 목록을 볼 때 사용한다.

    고정 제공되는 빌트인 규칙 3개를 맨 앞에 붙여서 함께 반환한다.
    빌트인 규칙은 list_rules()가 반환하는 개수에는 포함하지 않는다
    (조직이 직접 만들 수 있는 커스텀 규칙 한도와는 별개다).
    """

    custom_rules = (
        supabase.table("rules")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .execute()
    )

    for rule in custom_rules.data or []:
        rule["is_builtin"] = False

    return list_builtin_rules(organization_id) + (custom_rules.data or [])


def get_rule(
    organization_id: str,
    rule_id: str,
) -> dict | None:
    """
    특정 규칙 하나를 조회한다.
    """

    result = (
        supabase.table("rules")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", rule_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def update_rule(
    organization_id: str,
    rule_id: str,
    data: dict,
) -> dict | None:
    """
    특정 규칙을 수정한다.

    수정 가능한 값:
    - name
    - instruction
    - is_active
    """

    allowed_fields = {
        "name",
        "instruction",
        "is_active",
    }

    update_data = {
        key: value
        for key, value in data.items()
        if key in allowed_fields
    }

    if not update_data:
        return None

    update_data["updated_at"] = utc_now_iso()

    result = (
        supabase.table("rules")
        .update(update_data)
        .eq("organization_id", organization_id)
        .eq("id", rule_id)
        .execute()
    )

    if not result.data:
        return None

    _invalidate_active_rules_cache(organization_id)
    return result.data[0]


def delete_rule(
    organization_id: str,
    rule_id: str,
) -> bool:
    """
    특정 규칙을 삭제한다.
    """

    result = (
        supabase.table("rules")
        .delete()
        .eq("organization_id", organization_id)
        .eq("id", rule_id)
        .execute()
    )

    deleted = bool(result.data)
    if deleted:
        _invalidate_active_rules_cache(organization_id)
    return deleted


def get_active_rules(organization_id: str) -> list[dict]:
    """
    AI가 답변하기 전에 참고할 활성 규칙 목록을 조회한다.

    여기서는 사용자 메시지와 규칙을 비교하지 않는다.
    단순히 현재 조직에 등록된 활성 규칙을 가져오기만 한다.

    관리자가 가끔 수정하는 정적 메타데이터라 짧은 TTL로 캐싱하고,
    create/update/delete 시점에 바로 무효화해 지연을 최소화한다.
    """

    cached = _active_rules_cache.get(organization_id)
    now = time.monotonic()

    if cached is not None and now - cached[0] < _ACTIVE_RULES_CACHE_TTL_SECONDS:
        return cached[1]

    active_builtin_rules = [
        {"id": rule["id"], "name": rule["name"], "instruction": rule["instruction"]}
        for rule in list_builtin_rules(organization_id)
        if rule["is_active"]
    ]

    result = (
        supabase.table("rules")
        .select("id, name, instruction")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .order("created_at")
        .execute()
    )

    rules = active_builtin_rules + (result.data or [])
    _active_rules_cache[organization_id] = (now, rules)
    return rules