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
from app.rag.indexer import extract_keywords


logger = logging.getLogger(__name__)


# RPC에서 가져올 후보 개수를 match_count보다 넉넉히 두는 배수.
# match_knowledge_chunks가 similarity_threshold를 모르기 때문에,
# threshold 필터링 후에도 match_count개를 채울 수 있도록 여유분을 더 가져온다.
OVERFETCH_MULTIPLIER = 4

SIMILARITY_THRESHOLD = 0.25

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
    # 임베딩 + 키워드를 병렬로 추출
    query_embedding = await create_embedding(query)
    query_keywords = extract_keywords(query, max_keywords=10)

    cached_result = await _lookup_semantic_cache(organization_id, query_embedding, folder_id)
    if cached_result is not None:
        return cached_result[:match_count]

    # 하이브리드 검색: 키워드가 있으면 hybrid RPC, 없으면 기존 vector RPC
    if query_keywords:
        rpc_params = {
            "query_embedding": query_embedding,
            "query_keywords": query_keywords,
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

    filtered_rows = [
        row
        for row in rows
        if row.get("similarity", 0) >= SIMILARITY_THRESHOLD
    ]

    final_rows = filtered_rows[:match_count]

    _store_semantic_cache_background(organization_id, query_embedding, final_rows, folder_id)

    return final_rows


# 음성 통화에서 한 번에 듣기 부담스럽지 않은 길이. 너무 짧으면 정보가
# 부족해 보이고, 너무 길면(실측 494자, chunk 여러 개를 그대로 이어붙인 경우)
# 듣다가 맥락을 놓치거나 서로 다른 주제가 섞여 엉뚱한 답처럼 들린다.
KNOWLEDGE_ANSWER_MAX_CHARS = 150


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
    # 본문 첫 줄이 "설명: ~"인 경우가 많아, heading_name과 합치면
    # "이사 청소: 설명: ~"처럼 라벨이 중복된다 - "설명:" 라벨은 떼어낸다.
    plain_text = re.sub(r"^설명\s*:\s*", "", plain_text)

    if heading_name:
        plain_text = f"{heading_name}: {plain_text}" if plain_text else heading_name

    if len(plain_text) <= KNOWLEDGE_ANSWER_MAX_CHARS:
        return plain_text

    # 길이 제한 안에서 마지막 문장 끝(. ! ?)을 찾아 자연스럽게 끊는다.
    truncated = plain_text[:KNOWLEDGE_ANSWER_MAX_CHARS]
    last_sentence_end = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_sentence_end > 0:
        return truncated[: last_sentence_end + 1]
    return truncated.rstrip() + "..."
