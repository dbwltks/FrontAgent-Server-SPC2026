import asyncio
import logging
import threading

# 질문 embedding 생성
# → semantic cache(의미상 비슷한 과거 질문) 조회, 있으면 RPC 생략
# → 없으면 Supabase RPC 검색 → 결과를 semantic cache에 저장
# → 관련 chunk 5개 반환

from app.core.db import supabase
from app.providers.embedding_provider import create_embedding


logger = logging.getLogger(__name__)


# RPC에서 가져올 후보 개수를 match_count보다 넉넉히 두는 배수.
# match_knowledge_chunks가 similarity_threshold를 모르기 때문에,
# threshold 필터링 후에도 match_count개를 채울 수 있도록 여유분을 더 가져온다.
OVERFETCH_MULTIPLIER = 4

SIMILARITY_THRESHOLD = 0.35

# "영업시간이 언제예요?"와 "몇 시까지 하나요?"처럼 표현이 달라도 같은 의미면
# RAG 검색(임베딩 API 호출은 이미 했으니, 이후의 Supabase RPC)을 건너뛰기 위한
# semantic cache 매칭 threshold. 너무 낮으면 다른 질문에 엉뚱한 캐시를 줄 수 있어
# 보수적으로 잡는다.
SEMANTIC_CACHE_THRESHOLD = 0.93


async def _lookup_semantic_cache(
    organization_id: str,
    query_embedding: list[float],
    folder_id: str | None,
) -> list[dict] | None:
    try:
        result = await asyncio.to_thread(
            lambda: supabase.rpc(
                "match_knowledge_semantic_cache",
                {
                    "query_embedding": query_embedding,
                    "match_organization_id": organization_id,
                    "match_folder_id": folder_id,
                    "match_threshold": SEMANTIC_CACHE_THRESHOLD,
                },
            ).execute()
        )
    except Exception:
        logger.warning("semantic cache lookup failed", exc_info=True)
        return None

    rows = result.data or []
    if not rows:
        return None

    top = rows[0]
    if top.get("similarity", 0) < SEMANTIC_CACHE_THRESHOLD:
        return None

    cached_result = top.get("result")
    # 과거 버전이나 수동 입력으로 빈 결과 캐시가 남아 있으면 캐시 miss로 취급한다.
    # 빈 결과를 hit로 반환하면 이후 실제 지식이 추가되어도 RPC 검색을 건너뛸 수 있다.
    if not cached_result:
        return None

    return cached_result


def _store_semantic_cache_background(
    organization_id: str,
    query_embedding: list[float],
    result: list[dict],
    folder_id: str | None,
) -> None:
    # 검색 결과가 0건인 경우는 캐싱하지 않는다. RAG가 그 순간 우연히 못 찾았거나
    # threshold 미달이었을 수 있는데, 이걸 캐싱하면 이후 비슷한 질문 전부가
    # "검색 결과 없음"을 그대로 돌려받게 되어 실제로는 지식이 있어도 못 찾게 된다.
    if not result:
        return

    # 응답 경로(retrieve_knowledge)를 막지 않도록 백그라운드 스레드에서 저장한다.
    # 캐시 저장 실패/지연이 이번 턴의 검색 결과 반환을 늦추면 안 된다.
    def _insert():
        try:
            supabase.table("knowledge_semantic_cache").insert(
                {
                    "organization_id": organization_id,
                    "folder_id": folder_id,
                    "query_embedding": query_embedding,
                    "result": result,
                }
            ).execute()
        except Exception:
            logger.warning("semantic cache store failed", exc_info=True)

    threading.Thread(target=_insert, daemon=True).start()


async def retrieve_knowledge(
    organization_id: str,
    query: str,
    match_count: int = 5,
    folder_id: str | None = None,
) -> list[dict]:
    query_embedding = await create_embedding(query)

    cached_result = await _lookup_semantic_cache(organization_id, query_embedding, folder_id)
    if cached_result is not None:
        return cached_result[:match_count]

    rpc_params = {
        "query_embedding": query_embedding,
        "match_organization_id": organization_id,
        "match_count": match_count * OVERFETCH_MULTIPLIER,
        "match_folder_id": folder_id,
    }

    result = await asyncio.to_thread(
        lambda: supabase.rpc(
            "match_knowledge_chunks",
            rpc_params,
        ).execute()
    )

    rows = result.data or []

    filtered_rows = [
        row
        for row in rows
        if row.get("similarity", 0) >= SIMILARITY_THRESHOLD
    ]

    final_rows = filtered_rows[:match_count]

    _store_semantic_cache_background(organization_id, query_embedding, final_rows, folder_id)

    return final_rows
