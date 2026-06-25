from app.core.db import supabase
from app.repositories.organization_ai_settings_repo import get_ai_settings


def get_organization(organization_id: str) -> dict | None:
    result = (
        supabase.table("organizations")
        .select("id, name, llm_provider, llm_model")
        .eq("id", organization_id)
        .single()
        .execute()
    )
    organization = result.data
    if not organization:
        return None

    ai_settings = get_ai_settings(organization_id)

    return {
        **organization,
        "llm_provider": ai_settings.get("llm_provider") or organization.get("llm_provider"),
        "llm_model": ai_settings.get("llm_model") or organization.get("llm_model"),
        "decision_model": ai_settings.get("decision_model"),
        "voice_response_style": ai_settings.get("voice_response_style", "friendly_short"),
    }
