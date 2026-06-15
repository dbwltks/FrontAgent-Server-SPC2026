from app.core.db import supabase


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