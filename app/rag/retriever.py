import asyncio
import hashlib
import json
import logging
import math
import re

# 질문 embedding 생성
# → Redis semantic cache(의미상 비슷한 과거 질문) 조회, 있으면 RPC 생략
# → 없으면 하이브리드 RPC(벡터 + 키워드) 검색 → 결과를 Redis에 저장
# → 관련 chunk 반환

from app.core.db import supabase
from app.core.redis import redis_bytes_client
from app.providers.embedding_provider import create_embedding
from app.rag.indexer import extract_keywords, prepare_keywords_for_hybrid_rpc
from app.rag.query_matching import keyword_hits_in_content
from app.rag.keyword_vocabulary import get_organization_keyword_vocabulary


logger = logging.getLogger(__name__)

# Redis semantic cache TTL: 지식 업데이트 주기를 고려해 1시간
_SEMANTIC_CACHE_TTL = 60 * 60


# RPC에서 가져올 후보 개수를 match_count보다 넉넉히 두는 배수.
# threshold 필터링 후에도 match_count개를 채울 수 있도록 여유분을 더 가져온다.
OVERFETCH_MULTIPLIER = 2

# hybrid RPC combined score(0.7 vector + 0.3 keyword) 기준. 실측상 무관 chunk가
# 0.25~0.27대에 몰려 있어 0.30이 precision/recall 균형이 좋다.
HYBRID_SIMILARITY_THRESHOLD = 0.30
# 키워드 보정 없는 vector-only 검색은 더 보수적으로.
VECTOR_SIMILARITY_THRESHOLD = 0.35
# hybrid 결과는 content lexical evidence도 함께 요구한다.
MIN_KEYWORD_HITS_FOR_HYBRID = 2

# "영업시간이 언제예요?"와 "몇 시까지 하나요?"처럼 표현이 달라도 같은 의미면
# RAG 검색(임베딩 API 호출은 이미 했으니, 이후의 Supabase RPC)을 건너뛰기 위한
# semantic cache 매칭 threshold. 너무 낮으면 다른 질문에 엉뚱한 캐시를 줄 수 있어
# 보수적으로 잡는다.
SEMANTIC_CACHE_THRESHOLD = 0.93


def _keyword_hits_in_content(
    content: str,
    query_keywords: list[str],
    *,
    chunk_keywords: list[str] | None = None,
    vocabulary=None,
) -> int:
    return keyword_hits_in_content(
        content,
        query_keywords,
        chunk_keywords=chunk_keywords,
        vocabulary=vocabulary,
    )


def _required_keyword_hits(query_keywords: list[str]) -> int:
    if not query_keywords:
        return 0
    return min(MIN_KEYWORD_HITS_FOR_HYBRID, len(query_keywords))


def _passes_retrieval_threshold(
    row: dict,
    query_keywords: list[str],
    *,
    used_hybrid: bool,
    vocabulary=None,
) -> bool:
    similarity = row.get("similarity", 0)
    if used_hybrid:
        if similarity < HYBRID_SIMILARITY_THRESHOLD:
            return False
        content = row.get("content") or ""
        hits = _keyword_hits_in_content(
            content,
            query_keywords,
            chunk_keywords=row.get("keywords"),
            vocabulary=vocabulary,
        )
        required = _required_keyword_hits(query_keywords)
        # keyword가 있으면 반드시 1개 이상 hit해야 통과 — similarity가 높아도 예외 없음.
        # sim >= 0.50 무조건 통과는 "이사 청소" 쿼리에서 콜비 청크(sim=0.618, hit=0)를
        # 통과시키는 오염의 원인이었다(실측).
        if hits == 0:
            return False
        # hit 1개 이상이고 similarity가 0.40 이상이면 통과.
        # "A/S 신청 방법" 쿼리 → A/S 청크 sim≈0.49, hit=1(a/s) 케이스 대응.
        if hits >= 1 and similarity >= 0.40:
            return True
        return hits >= required

    return similarity >= VECTOR_SIMILARITY_THRESHOLD


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _semantic_cache_key(organization_id: str, folder_id: str | None) -> str:
    folder_part = folder_id or "all"
    return f"rag_scache:{organization_id}:{folder_part}"


def clear_semantic_cache(organization_id: str, folder_id: str | None = None) -> None:
    pattern = _semantic_cache_key(organization_id, folder_id) + ":*"
    keys = redis_bytes_client.keys(pattern)
    if keys:
        redis_bytes_client.delete(*keys)


def _resolve_semantic_cache_sync(
    query_embedding: list[float],
    cache_entries: list[bytes],
    query_keywords: list[str] | None = None,
    vocabulary=None,
) -> list[dict] | None:
    """미리 fetch된 Redis 항목들에서 cosine similarity로 최적 캐시를 선택한다."""
    if not cache_entries:
        return None

    best_sim = -1.0
    best_cached: list[dict] | None = None

    for raw in cache_entries:
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        stored_emb: list[float] = entry["embedding"]
        sim = _cosine_similarity(query_embedding, stored_emb)
        if sim > best_sim:
            best_sim = sim
            best_cached = entry["result"]

    if best_sim < SEMANTIC_CACHE_THRESHOLD or not best_cached:
        return None

    if query_keywords:
        top_chunk = best_cached[0]
        top_sim = top_chunk.get("similarity", 0)
        if top_sim < HYBRID_SIMILARITY_THRESHOLD:
            return None
        content = top_chunk.get("content") or ""
        hits = _keyword_hits_in_content(
            content,
            query_keywords,
            chunk_keywords=top_chunk.get("keywords"),
            vocabulary=vocabulary,
        )
        required = _required_keyword_hits(query_keywords)
        if hits == 0:
            return None
        if hits < required and top_sim < 0.50:
            return None

    return best_cached


async def _resolve_semantic_cache(
    query_embedding: list[float],
    cache_entries: list[bytes],
    query_keywords: list[str] | None = None,
    vocabulary=None,
) -> list[dict] | None:
    return _resolve_semantic_cache_sync(query_embedding, cache_entries, query_keywords, vocabulary)


def _store_semantic_cache_background(
    organization_id: str,
    query_embedding: list[float],
    result: list[dict],
    folder_id: str | None,
) -> None:
    # 검색 결과가 0건이면 캐싱하지 않는다.
    if not result:
        return

    emb_hash = hashlib.sha1(json.dumps(query_embedding[:8]).encode()).hexdigest()[:12]
    key = _semantic_cache_key(organization_id, folder_id) + f":{emb_hash}"
    payload = json.dumps({"embedding": query_embedding, "result": result})

    try:
        redis_bytes_client.setex(key, _SEMANTIC_CACHE_TTL, payload.encode())
    except Exception:
        logger.warning("semantic cache store failed", exc_info=True)


async def _fetch_semantic_cache_entries(organization_id: str, folder_id: str | None) -> list[bytes]:
    """embedding 계산 중에 미리 Redis에서 캐시 후보를 가져온다."""
    try:
        prefix = _semantic_cache_key(organization_id, folder_id) + ":"

        def _pipeline_get():
            keys = redis_bytes_client.keys(prefix + "*")
            if not keys:
                return []
            pipe = redis_bytes_client.pipeline(transaction=False)
            for k in keys:
                pipe.get(k)
            return pipe.execute()

        return await asyncio.to_thread(_pipeline_get)
    except Exception:
        return []


async def retrieve_knowledge(
    organization_id: str,
    query: str,
    match_count: int = 5,
    folder_id: str | None = None,
) -> list[dict]:
    vocabulary = get_organization_keyword_vocabulary(organization_id)
    query_keywords = extract_keywords(query, max_keywords=10, vocabulary=vocabulary, for_query=True)

    # embedding과 Redis 캐시 항목 fetch를 병렬로 실행
    query_embedding, cache_entries = await asyncio.gather(
        create_embedding(query),
        _fetch_semantic_cache_entries(organization_id, folder_id),
    )

    cached_result = await _resolve_semantic_cache(
        query_embedding, cache_entries, query_keywords, vocabulary
    )

    if cached_result is not None:
        used_hybrid = bool(query_keywords)
        filtered_cache = [
            row
            for row in cached_result
            if _passes_retrieval_threshold(
                row,
                query_keywords,
                used_hybrid=used_hybrid,
                vocabulary=vocabulary,
            )
        ]
        if filtered_cache:
            return filtered_cache[:match_count]

    # 하이브리드 검색: 키워드가 있으면 hybrid RPC, 없으면 기존 vector RPC
    if query_keywords:
        rpc_params = {
            "query_embedding": query_embedding,
            "query_keywords": prepare_keywords_for_hybrid_rpc(query_keywords),
            "match_organization_id": organization_id,
            "match_count": match_count * OVERFETCH_MULTIPLIER,
            "match_folder_id": folder_id,
        }
        result = await asyncio.to_thread(
            lambda: supabase.rpc("match_knowledge_chunks_hybrid", rpc_params).execute()
        )
    else:
        rpc_params = {
            "query_embedding": query_embedding,
            "match_organization_id": organization_id,
            "match_count": match_count * OVERFETCH_MULTIPLIER,
            "match_folder_id": folder_id,
        }
        result = await asyncio.to_thread(
            lambda: supabase.rpc("match_knowledge_chunks", rpc_params).execute()
        )
    rows = result.data or []
    used_hybrid = bool(query_keywords)

    filtered_rows = [
        row
        for row in rows
        if _passes_retrieval_threshold(
            row,
            query_keywords,
            used_hybrid=used_hybrid,
            vocabulary=vocabulary,
        )
    ]

    final_rows = filtered_rows[:match_count]
    # 백그라운드로 Redis에 저장 — 이번 응답 속도에 영향 없음
    asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _store_semantic_cache_background(organization_id, query_embedding, final_rows, folder_id),
    )

    return final_rows


# 음성 통화에서 한 번에 듣기 부담스럽지 않은 길이. 너무 짧으면 정보가
# 부족해 보이고, 너무 길면(실측 494자, chunk 여러 개를 그대로 이어붙인 경우)
# 듣다가 맥락을 놓치거나 서로 다른 주제가 섞여 엉뚱한 답처럼 들린다.
KNOWLEDGE_ANSWER_MAX_CHARS = 150

_POLITE_SENTENCE_ENDINGS = ("요", "다", "니다", "세요", "까요", "?", "!", ".", "…")


def _ensure_natural_korean_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return text
    if text.endswith(_POLITE_SENTENCE_ENDINGS):
        return text
    if re.search(r"\d+(?:,\d{3})*\s*원", text):
        return f"{text}이에요."
    return f"{text}예요."


def _weave_service_name_into_answer(heading_name: str | None, plain_text: str) -> str:
    text = re.sub(r"\s+", " ", (plain_text or "").strip())
    if not text:
        return (heading_name or "").strip()

    if not heading_name:
        return _ensure_natural_korean_sentence(text)

    heading = heading_name.strip()
    collapsed_heading = re.sub(r"\s+", "", heading.lower())
    collapsed_text = re.sub(r"\s+", "", text.lower())

    if collapsed_text.startswith(collapsed_heading):
        return _ensure_natural_korean_sentence(text)

    prefix_window = collapsed_text[: len(collapsed_heading) + 8]
    if collapsed_heading in prefix_window:
        return _ensure_natural_korean_sentence(text)

    body = re.sub(r"^설명\s*:\s*", "", text).strip()
    if body.endswith("."):
        body = body[:-1].strip()

    if re.search(r"(은|는|이|가)\s", body[: max(len(heading) + 2, 8)]):
        return _ensure_natural_korean_sentence(body)

    if re.search(r"(가격|요금|비용).*(원|\d)", body):
        price_match = re.search(
            r"기본\s*가격\s*:\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*원",
            body,
        )
        if price_match:
            amount = price_match.group(1)
            return _ensure_natural_korean_sentence(f"{heading}는 기본 가격이 {amount}원")
        return _ensure_natural_korean_sentence(f"{heading}는 {body}")

    return _ensure_natural_korean_sentence(f"{heading}는 {body}")


def summarize_knowledge_chunk(chunk: dict) -> str | None:
    """
    검색된 chunk 하나를 음성/채팅으로 듣기 좋은 짧은 답변으로 정리한다. 2차
    LLM 호출로 자연스럽게 요약하는 대신(속도 우선) 마크다운 헤더/옵션 목록
    같은 구조적 텍스트를 코드로 제거하고 적당한 길이에서 문장 단위로 자른다.

    여러 chunk를 그대로 이어붙이면(특히 realtime 음성 통화) 서로 다른 주제가
    섞여 모델이 엉뚱한 내용까지 같이 말하는 사례가 실측됐다 - 호출하는 쪽은
    가장 유사도 높은 chunk 1개만 넘겨야 한다.
    """
    content = (chunk.get("content") or "").strip()
    if not content:
        return None

    # "### 서비스 아이템: 이사 청소" 같은 마크다운 헤더는 "이건 어떤 항목에
    # 대한 설명인지" 알려주는 핵심 정보다 - 그냥 버리면 "설명: ~입니다"만
    # 남아 어느 서비스 얘기인지 알 수 없는 답변이 된다(실측 사례). 헤더
    # 기호와 "서비스 아이템:"/"서비스 카테고리:" 같은 라벨만 떼어 이름만
    # 답변 맨 앞에 붙인다.
    heading_name: str | None = None
    heading_match = re.search(
        r"^#{1,6}\s*(?:서비스\s*아이템|서비스\s*카테고리)?\s*:?\s*(\S.*)$",
        content,
        re.MULTILINE,
    )
    if heading_match:
        heading_name = heading_match.group(1).strip()

    # "### 제목", "- 옵션: 값" 같은 구조적 줄, 그리고 "1. 영업시간"처럼 숫자
    # 목록 제목만 있는 줄, "프리미엄 청소 FAQ 테스트 문서"처럼 본문 없이
    # 파일 제목만 있는 첫 줄은 듣기/읽기에 부적합하므로 일반 문장으로 보이는
    # 줄만 남긴다(문장부호로 안 끝나는 5단어 이하 줄은 제목으로 간주).
    def _looks_like_heading(line: str) -> bool:
        if re.search(r"(기본\s*가격|요금|소요\s*시간).*(원|분|\d)", line):
            return False
        if re.match(r"^\d+[.)]\s*\S+$", line) and len(line.split()) <= 4:
            return True
        if not line.endswith((".", "!", "?", "다", "요", "임", "음")) and len(line.split()) <= 6:
            return True
        return False

    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip()
        and not line.strip().startswith(("#", "-", "*"))
        and not _looks_like_heading(line.strip())
    ]
    plain_text = " ".join(lines) if lines else re.sub(r"[#*\-]+", " ", content)
    plain_text = re.sub(r"\s+", " ", plain_text).strip()
    # 본문 첫 줄이 "설명: ~"인 경우가 많아 "설명:" 라벨은 떼어낸다.
    plain_text = re.sub(r"^설명\s*:\s*", "", plain_text)

    plain_text = _weave_service_name_into_answer(heading_name, plain_text)

    if len(plain_text) <= KNOWLEDGE_ANSWER_MAX_CHARS:
        return plain_text

    # 길이 제한 안에서 마지막 문장 끝(. ! ?)을 찾아 자연스럽게 끊는다.
    truncated = plain_text[:KNOWLEDGE_ANSWER_MAX_CHARS]
    last_sentence_end = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_sentence_end > 0:
        return truncated[: last_sentence_end + 1]
    return truncated.rstrip() + "..."
