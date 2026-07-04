import asyncio
import logging
import re
import threading

# 질문 embedding 생성
# → semantic cache(의미상 비슷한 과거 질문) 조회, 있으면 RPC 생략
# → 없으면 하이브리드 RPC(벡터 + 키워드) 검색 → 결과를 semantic cache에 저장
# → 관련 chunk 반환

from app.core.db import supabase
from app.providers.embedding_provider import create_embedding
from app.rag.indexer import extract_keywords, prepare_keywords_for_hybrid_rpc
from app.rag.query_matching import keyword_hits_in_content
from app.rag.keyword_vocabulary import get_organization_keyword_vocabulary


logger = logging.getLogger(__name__)


# RPC에서 가져올 후보 개수를 match_count보다 넉넉히 두는 배수.
# match_knowledge_chunks가 similarity_threshold를 모르기 때문에,
# threshold 필터링 후에도 match_count개를 채울 수 있도록 여유분을 더 가져온다.
OVERFETCH_MULTIPLIER = 4

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
        return _keyword_hits_in_content(
            content,
            query_keywords,
            chunk_keywords=row.get("keywords"),
            vocabulary=vocabulary,
        ) >= _required_keyword_hits(query_keywords)

    return similarity >= VECTOR_SIMILARITY_THRESHOLD


def clear_semantic_cache(organization_id: str, folder_id: str | None = None) -> None:
    query = supabase.table("knowledge_semantic_cache").delete().eq("organization_id", organization_id)
    if folder_id:
        query = query.eq("folder_id", folder_id)
    query.execute()


async def _lookup_semantic_cache(
    organization_id: str,
    query_embedding: list[float],
    folder_id: str | None,
    query_keywords: list[str] | None = None,
    vocabulary=None,
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

    # 지식/검색 로직이 바뀐 뒤에도 예전 오답 캐시가 남아 있을 수 있다.
    # query keyword와 전혀 맞지 않는 cached chunk면 miss로 처리한다.
    if query_keywords:
        top_chunk = cached_result[0]
        content = top_chunk.get("content") or ""
        if _keyword_hits_in_content(
            content,
            query_keywords,
            chunk_keywords=top_chunk.get("keywords"),
            vocabulary=vocabulary,
        ) < _required_keyword_hits(query_keywords):
            return None
        if top_chunk.get("similarity", 0) < HYBRID_SIMILARITY_THRESHOLD:
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
    # 임베딩 + 키워드를 병렬로 추출
    query_embedding = await create_embedding(query)
    vocabulary = get_organization_keyword_vocabulary(organization_id)
    query_keywords = extract_keywords(
        query,
        max_keywords=10,
        vocabulary=vocabulary,
        for_query=True,
    )

    cached_result = await _lookup_semantic_cache(
        organization_id,
        query_embedding,
        folder_id,
        query_keywords,
        vocabulary,
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

    _store_semantic_cache_background(organization_id, query_embedding, final_rows, folder_id)

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
