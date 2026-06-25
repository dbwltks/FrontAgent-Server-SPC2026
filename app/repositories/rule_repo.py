import time
from datetime import datetime, timezone

from app.core.db import supabase

# 활성 규칙은 organization 설정(app/providers/langchain_provider.py의
# _organization_cache)과 같은 성격의 작은 정적 메타데이터다. 워커 프로세스
# 안에서만 유효한 인메모리 TTL 캐시로 충분하고, Redis 왕복을 새로 추가할
# 만큼 무겁거나 여러 워커 간 즉시 동기화가 필요한 데이터가 아니다.
_ACTIVE_RULES_CACHE_TTL_SECONDS = 60
_active_rules_cache: dict[str, tuple[float, list[dict]]] = {}


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
    """

    result = (
        supabase.table("rules")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .execute()
    )

    return result.data or []


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

    result = (
        supabase.table("rules")
        .select("id, name, instruction")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .order("created_at")
        .execute()
    )

    rules = result.data or []
    _active_rules_cache[organization_id] = (now, rules)
    return rules