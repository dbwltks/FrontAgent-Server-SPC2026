from app.core.db import supabase


def list_knowledge_sources(
    organization_id: str,
    folder_id: str | None = None,
) -> list[dict]:
    query = (
        supabase.table("knowledge_sources")
        .select("*")
        .eq("organization_id", organization_id)
    )

    if folder_id:
        query = query.eq("folder_id", folder_id)

    result = query.order("created_at", desc=True).execute()

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


def increment_reference_counts(source_ids: list[str]) -> None:
    for source_id in set(source_ids):
        supabase.rpc(
            "increment_knowledge_reference_count",
            {"source_id_input": source_id},
        ).execute()


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

def delete_knowledge_chunks(
    *,
    organization_id: str,
    source_id: str,
) -> bool:
    result = (
        supabase.table("knowledge_chunks")
        .delete()
        .eq("organization_id", organization_id)
        .eq("source_id", source_id)
        .execute()
    )

    return bool(result.data)

def get_knowledge_source_by_checksum(
    organization_id: str,
    checksum_sha256: str,
) -> dict | None:
    result = (
        supabase.table("knowledge_sources")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("checksum_sha256", checksum_sha256)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]