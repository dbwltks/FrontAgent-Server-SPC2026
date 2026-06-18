# source 생성
# → chunking
# → embedding 생성
# → knowledge_chunks 저장

from app.core.db import supabase
from app.providers.embedding_provider import create_embeddings_batch
from app.rag.chunker import chunk_text


def create_knowledge_source(
    organization_id: str,
    title: str,
    source_type: str = "text",
    folder_id: str | None = None,
    file_name: str | None = None,
    mime_type: str | None = None,
) -> str:
    result = supabase.table("knowledge_sources").insert({
        "organization_id": organization_id,
        "folder_id": folder_id,
        "title": title,
        "source_type": source_type,
        "file_name": file_name,
        "mime_type": mime_type,
        "status": "processing",
        "is_referenced": True,
    }).execute()

    return result.data[0]["id"]


def update_source_status(source_id: str, status: str) -> None:
    supabase.table("knowledge_sources").update({
        "status": status,
    }).eq("id", source_id).execute()


def index_text(
    organization_id: str,
    title: str,
    text: str,
    folder_id: str | None = None,
    source_type: str = "text",
    file_name: str | None = None,
    mime_type: str | None = None,
) -> dict:
    source_id = create_knowledge_source(
        organization_id=organization_id,
        title=title,
        source_type=source_type,
        folder_id=folder_id,
        file_name=file_name,
        mime_type=mime_type,
    )

    try:
        update_source_status(source_id, "chunking")
        chunks = chunk_text(text)

        update_source_status(source_id, "embedding")
        embeddings = create_embeddings_batch(chunks)

        rows = []

        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            rows.append({
                "organization_id": organization_id,
                "source_id": source_id,
                "folder_id": folder_id,
                "chunk_index": index,
                "content": chunk,
                "embedding": embedding,
                "metadata": {
                    "title": title,
                    "source_type": source_type,
                    "file_name": file_name,
                    "chunk_length": len(chunk),
                },
            })

        if rows:
            supabase.table("knowledge_chunks").insert(rows).execute()

        update_source_status(source_id, "indexed")

    except Exception:
        update_source_status(source_id, "failed")
        raise

    return {
        "source_id": source_id,
        "chunks": len(rows),
        "status": "indexed",
    }
