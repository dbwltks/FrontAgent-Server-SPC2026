from app.core.db import supabase

# rules 테이블 전용 DB 함수 모음


def create_rule(data: dict) -> dict:
    result = (
        supabase.table("rules")
        .insert(data)
        .execute()
    )

    if not result.data:
        return {}

    return result.data[0]


def list_rules(organization_id: str) -> list[dict]:
    result = (
        supabase.table("rules")
        .select("*")
        .eq("organization_id", organization_id)
        .order("priority", desc=True)
        .execute()
    )

    return result.data or []


def get_rule(organization_id: str, rule_id: str) -> dict | None:
    result = (
        supabase.table("rules")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", rule_id)
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
    result = (
        supabase.table("rules")
        .update(data)
        .eq("organization_id", organization_id)
        .eq("id", rule_id)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def delete_rule(organization_id: str, rule_id: str) -> bool:
    result = (
        supabase.table("rules")
        .delete()
        .eq("organization_id", organization_id)
        .eq("id", rule_id)
        .execute()
    )

    return bool(result.data)


def get_active_rules(organization_id: str) -> list[dict]:
    result = (
        supabase.table("rules")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .order("priority", desc=True)
        .execute()
    )

    return result.data or []