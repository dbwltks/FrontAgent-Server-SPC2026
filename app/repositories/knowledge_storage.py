from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.db import supabase


MIME_TYPES_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def content_type_for_file(file_name: str, provided_content_type: str | None) -> str:
    suffix = Path(file_name).suffix.lower()
    return MIME_TYPES_BY_EXTENSION.get(suffix, provided_content_type or "application/octet-stream")


def build_knowledge_storage_path(
    organization_id: str,
    source_id: str,
    file_name: str,
) -> str:
    suffix = Path(file_name).suffix.lower()
    return f"{organization_id}/{source_id}/{uuid4().hex}{suffix}"


def upload_knowledge_original(
    local_file_path: str,
    storage_path: str,
    content_type: str,
) -> None:
    with open(local_file_path, "rb") as file:
        supabase.storage.from_(settings.knowledge_storage_bucket).upload(
            path=storage_path,
            file=file,
            file_options={
                "content-type": content_type,
                "upsert": "false",
            },
        )


def delete_knowledge_original(storage_bucket: str, storage_path: str) -> None:
    supabase.storage.from_(storage_bucket).remove([storage_path])


def create_knowledge_download_url(
    storage_bucket: str,
    storage_path: str,
    expires_in: int = 300,
) -> str:
    response = supabase.storage.from_(storage_bucket).create_signed_url(
        storage_path,
        expires_in,
    )
    signed_url = response.get("signedURL") or response.get("signedUrl")

    if not signed_url:
        raise RuntimeError("Supabase Storage did not return a signed URL")

    return signed_url
