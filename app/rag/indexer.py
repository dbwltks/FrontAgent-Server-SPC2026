# source 생성
# → chunking
# → keyword 추출
# → embedding 생성
# → knowledge_chunks 저장

import re

from app.core.db import supabase
from app.providers.embedding_provider import create_embeddings_batch
from app.rag.chunker import chunk_text


def extract_keywords(text: str, max_keywords: int = 20) -> list[str]:
    """
    chunk 텍스트에서 검색에 유용한 키워드를 추출한다.
    - 한국어 명사/고유명사 패턴 (2자 이상 연속 한글)
    - 숫자+단위 조합 (가격, 시간 등)
    - 영문 단어
    불용어(조사, 접속사 등)는 제외한다.
    """
    stopwords = {
        "이다", "있다", "없다", "하다", "되다", "이고", "이며", "또는", "그리고",
        "하지만", "그러나", "때문에", "위해", "통해", "대한", "관한", "위한",
        "기준", "경우", "내용", "정보", "안내", "서비스", "고객", "담당자",
    }

    keywords = set()

    # 한글 2자 이상 토큰
    for word in re.findall(r"[가-힣]{2,}", text):
        if word not in stopwords and len(word) <= 10:
            keywords.add(word)

    # 숫자+단위 (가격, 시간, 평수 등)
    for match in re.findall(r"\d+(?:,\d{3})*(?:원|분|평|개|명|시간|일|개월)?", text):
        if match:
            keywords.add(match)

    # 영문 단어 (3자 이상)
    for word in re.findall(r"[A-Za-z]{3,}", text):
        keywords.add(word.lower())

    return list(keywords)[:max_keywords]


def create_knowledge_source(
    organization_id: str,
    title: str,
    source_type: str = "text",
    folder_id: str | None = None,
    file_name: str | None = None,
    mime_type: str | None = None,
    source_id: str | None = None,
    storage_bucket: str | None = None,
    storage_path: str | None = None,
    file_size: int | None = None,
    checksum_sha256: str | None = None,
    status: str = "processing",
) -> str:
    row = {
        "organization_id": organization_id,
        "folder_id": folder_id,
        "title": title,
        "source_type": source_type,
        "file_name": file_name,
        "mime_type": mime_type,
        "storage_bucket": storage_bucket,
        "storage_path": storage_path,
        "file_size": file_size,
        "checksum_sha256": checksum_sha256,
        "status": status,
        "is_referenced": True,
    }

    if source_id:
        row["id"] = source_id

    result = supabase.table("knowledge_sources").insert(row).execute()

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
    source_id: str | None = None,
) -> dict:
    if source_id is None:
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
                "keywords": extract_keywords(chunk),
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
