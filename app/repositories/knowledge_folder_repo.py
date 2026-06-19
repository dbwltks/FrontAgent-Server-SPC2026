from app.core.db import supabase


def create_knowledge_folder(data: dict) -> dict | None:
    result = (
        supabase.table("knowledge_folders")
        .insert(data)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def list_knowledge_folders(organization_id: str) -> list[dict]:
    result = (
        supabase.table("knowledge_folders")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=False)
        .execute()
    )

    return result.data or []


def get_knowledge_folder(
    organization_id: str,
    folder_id: str,
) -> dict | None:
    result = (
        supabase.table("knowledge_folders")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", folder_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def update_knowledge_folder(
    organization_id: str,
    folder_id: str,
    data: dict,
) -> dict | None:
    result = (
        supabase.table("knowledge_folders")
        .update(data)
        .eq("organization_id", organization_id)
        .eq("id", folder_id)
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def delete_knowledge_folder(
    organization_id: str,
    folder_id: str,
) -> bool:
    result = (
        supabase.rpc(
            "delete_knowledge_folder_keep_contents",
            {
                "organization_id_input": organization_id,
                "folder_id_input": folder_id,
            },
        )
        .execute()
    )

    return bool(result.data)