from app.core.db import supabase
from app.repositories.organization_ai_settings_repo import get_ai_settings

# API 문서/테스트에서 example="org_test"를 그대로 복사해 보내는 경우가 많아,
# 실제 UUID가 아닌 이 별칭이 들어오면 기본 조직 UUID로 치환한다.
DEFAULT_ORGANIZATION_ALIAS = "org_test"
DEFAULT_ORGANIZATION_ID = "a55c98f9-74ba-40d8-bc9d-bc3f1c0870da"


def resolve_organization_id(organization_id: str) -> str:
    if organization_id == DEFAULT_ORGANIZATION_ALIAS:
        return DEFAULT_ORGANIZATION_ID
    return organization_id


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
