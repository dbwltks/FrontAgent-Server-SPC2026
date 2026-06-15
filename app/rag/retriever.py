# 질문 embedding 생성
# → Supabase RPC 검색
# → 관련 chunk 5개 반환

from app.core.db import supabase
from app.providers.embedding_provider import create_embedding


# RPC에서 가져올 후보 개수를 match_count보다 넉넉히 두는 배수.
# match_knowledge_chunks가 similarity_threshold를 모르기 때문에,
# threshold 필터링 후에도 match_count개를 채울 수 있도록 여유분을 더 가져온다.
OVERFETCH_MULTIPLIER = 4


def retrieve_knowledge(
    organization_id: str,
    query: str,
    match_count: int = 5, # 반환할 chunk 개수
    similarity_threshold: float = 0.1, # 유사도 임계값
) -> list[dict]:
    query_embedding = create_embedding(query)

    result = supabase.rpc(
        "match_knowledge_chunks",
        {
            "query_embedding": query_embedding,
            "match_organization_id": organization_id,
            "match_count": match_count * OVERFETCH_MULTIPLIER,
        },
    ).execute()

    rows = result.data or []

    filtered_rows = [
        row for row in rows
        if row.get("similarity", 0) >= similarity_threshold
    ]

    return filtered_rows[:match_count]