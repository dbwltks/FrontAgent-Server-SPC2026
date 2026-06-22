from app.core.db import supabase


def get_organization(organization_id: str) -> dict | None:
    result = (
        supabase.table("organizations")
        .select("id, name, llm_provider, llm_model, streaming_model")
        .eq("id", organization_id)
        .single()
        .execute()
    )
    return result.data
