from app.core.db import supabase


def list_knowledge_sources(organization_id: str) -> list[dict]:
    result = (
        supabase.table("knowledge_sources")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .execute()
    )

    return result.data or []


def get_knowledge_source(
    organization_id: str,
    source_id: str,
) -> dict | None:
    result = (
        supabase.table("knowledge_sources")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", source_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def update_knowledge_source(
    organization_id: str,
    source_id: str,
    data: dict,
) -> dict | None:
    result = (
        supabase.table("knowledge_sources")
        .update(data)
        .eq("organization_id", organization_id)
        .eq("id", source_id)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def delete_knowledge_source(
    organization_id: str,
    source_id: str,
) -> bool:
    result = (
        supabase.table("knowledge_sources")
        .delete()
        .eq("organization_id", organization_id)
        .eq("id", source_id)
        .execute()
    )

    return bool(result.data)


def list_knowledge_chunks(
    organization_id: str,
    source_id: str,
) -> list[dict]:
    result = (
        supabase.table("knowledge_chunks")
        .select("id, source_id, chunk_index, content, metadata, created_at")
        .eq("organization_id", organization_id)
        .eq("source_id", source_id)
        .order("chunk_index")
        .execute()
    )

    return result.data or []