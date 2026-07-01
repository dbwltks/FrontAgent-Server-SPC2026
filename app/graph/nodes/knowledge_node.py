import re
from typing import Any


def _normalize_query_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_knowledge_queries(
    user_message: str,
    generated_queries: list[str] | None,
    *,
    max_queries: int = 3,
) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    for query in [user_message, *(generated_queries or [])]:
        normalized = _normalize_query_text(query)
        key = normalized.replace(" ", "")
        if not normalized or key in seen:
            continue
        queries.append(normalized)
        seen.add(key)
        if len(queries) >= max_queries:
            break

    return queries


def merge_unique_chunks(
    knowledge_context_groups: list[dict[str, Any]],
    *,
    max_chunks: int = 6,
) -> list[dict[str, Any]]:
    chunks_by_id: dict[str, dict[str, Any]] = {}

    for group in knowledge_context_groups:
        for chunk in group.get("chunks") or []:
            chunk_id = str(chunk.get("id") or "")
            if not chunk_id:
                continue

            existing = chunks_by_id.get(chunk_id)
            if existing is None or (chunk.get("similarity") or 0) > (existing.get("similarity") or 0):
                chunks_by_id[chunk_id] = chunk

    return sorted(
        chunks_by_id.values(),
        key=lambda chunk: chunk.get("similarity") or 0,
        reverse=True,
    )[:max_chunks]
