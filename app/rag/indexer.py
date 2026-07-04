# source 생성
# → chunking
# → keyword 추출
# → embedding 생성
# → knowledge_chunks 저장

import re

from app.core.db import supabase
from app.providers.embedding_provider import create_embeddings_batch
from app.rag.chunker import chunk_text
from app.rag.query_matching import term_appears_in_text
from app.rag.keyword_vocabulary import (
    OrganizationKeywordVocabulary,
    UNIVERSAL_SYNONYM_GROUPS,
    get_organization_keyword_vocabulary,
)


_KOREAN_QUESTION_SUFFIX = re.compile(
    r"(얼마야|얼마예요|얼마에요|얼마인가요|얼마|인가요|인가|뭐야|뭐예요|뭐에요|무엇|어때|할까요|할까|인지)$"
)
_KOREAN_VERB_SUFFIX = re.compile(
    r"(합니다|입니다|됩니다|해요|하세요|주세요|드립니다|진행됩니다|포함합니다|가능합니다)$"
)
_SERVICE_HEADING_PATTERN = re.compile(
    r"^#{1,6}\s*(?:서비스\s*(?:아이템|카테고리))?\s*:?\s*(.+)$",
    re.MULTILINE,
)
_PRICE_PATTERN = re.compile(
    r"기본\s*가격\s*:\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*원",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"(?:예상\s*)?소요\s*시간\s*:\s*(\d+)\s*분",
    re.IGNORECASE,
)

_BASE_STOPWORDS = {
    "이다", "있다", "없다", "하다", "되다", "이고", "이며", "또는", "그리고",
    "하지만", "그러나", "때문에", "위해", "통해", "대한", "관한", "위한",
    "기준", "경우", "내용", "정보", "안내", "고객", "담당자",
    "에서", "으로", "로", "입니다", "했습니다", "결제했습니다", "카드로",
    "현금로", "계좌로", "담당", "정산", "미정산",
    "어떻게", "되나요", "되나", "있나요", "있나",
    "설명", "옵션", "포함", "진행", "확인", "작업", "상태", "전후",
    "희망", "여부", "종류", "아이템", "카테고리", "전체", "예상",
    "포함합니다", "진행됩니다", "서비스입니다", "원입니다", "가능합니다",
    "알려주시면", "정리해", "보관해", "필요한", "발생할", "늘어날",
    "진행하", "확인한", "연결해",
    "날짜", "시간", "유형", "금액", "통화", "결제수단",
    "거래처", "거래자", "정산여부", "지출", "수입", "펀드",
    "컬럼", "항목", "번호", "분류",
    "서비스",
}


def _expand_synonyms(word: str, vocabulary: OrganizationKeywordVocabulary | None) -> list[str]:
    normalized = word.strip().lower()
    if vocabulary:
        expanded = vocabulary.expand(normalized)
        if len(expanded) > 1 or expanded[0] != normalized:
            return expanded

    for group in UNIVERSAL_SYNONYM_GROUPS:
        if normalized in group:
            return sorted(group)
    return [normalized]


def _strip_korean_token(word: str) -> str:
    base = _KOREAN_QUESTION_SUFFIX.sub("", word).strip()
    base = _KOREAN_VERB_SUFFIX.sub("", base).strip()
    base = re.sub(r"(으로|로|에서|은|는|이|가|을|를|과|와)$", "", base).strip()
    return base


def _normalize_korean_keyword(
    word: str,
    vocabulary: OrganizationKeywordVocabulary | None,
) -> list[str]:
    base = _strip_korean_token(word)
    if not base or len(base) < 2:
        return []
    return _expand_synonyms(base, vocabulary)


def _append_keywords(target: list[str], words: list[str], stopwords: set[str]) -> None:
    for word in words:
        key = word.lower().strip()
        if key and key not in stopwords:
            target.append(key)


def _extract_vocabulary_keywords(
    text: str,
    vocabulary: OrganizationKeywordVocabulary,
    stopwords: set[str],
    *,
    expand_synonyms: bool = True,
) -> list[str]:
    """조직 vocabulary에 있는 용어가 chunk/질문에 등장하면 우선 추출."""
    keywords: list[str] = []

    for term in sorted(vocabulary.terms, key=len, reverse=True):
        if len(term) < 2 or term in stopwords:
            continue
        if term_appears_in_text(term, text):
            expanded = vocabulary.expand(term) if expand_synonyms else [term]
            _append_keywords(keywords, expanded, stopwords)

    return keywords


def _extract_structured_keywords(
    text: str,
    vocabulary: OrganizationKeywordVocabulary | None,
    stopwords: set[str],
    *,
    expand_synonyms: bool = True,
) -> list[str]:
    """마크다운 카탈로그·가격·시간 등 업종 공통 구조 패턴."""
    keywords: list[str] = []

    for match in _SERVICE_HEADING_PATTERN.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1).strip())
        if not name:
            continue
        _append_keywords(keywords, [name], stopwords)
        if expand_synonyms:
            _append_keywords(keywords, _expand_synonyms(name, vocabulary), stopwords)
        for part in re.findall(r"[가-힣]{2,}", name):
            if expand_synonyms:
                _append_keywords(keywords, _normalize_korean_keyword(part, vocabulary), stopwords)
            else:
                stripped = _strip_korean_token(part)
                if stripped:
                    _append_keywords(keywords, [stripped], stopwords)

    for match in _PRICE_PATTERN.finditer(text):
        _append_keywords(
            keywords,
            [f"{match.group(1).replace(',', '')}원", "가격", "요금"],
            stopwords,
        )

    for match in _DURATION_PATTERN.finditer(text):
        _append_keywords(keywords, [f"{match.group(1)}분", "소요시간"], stopwords)

    return keywords


def _extract_financial_keywords(text: str, stopwords: set[str]) -> list[str]:
    """재정/거래 내역 chunk용 (해당 데이터가 있는 조직에서만 의미 있음)."""
    keywords: list[str] = []
    for match in re.finditer(r"지출\s+(.+?)에서", text):
        merchant = re.sub(r"\s+", " ", match.group(1).strip())
        if merchant and merchant not in stopwords:
            _append_keywords(keywords, [merchant], stopwords)
            for word in re.findall(r"[A-Za-z]{3,}", merchant):
                _append_keywords(keywords, [word.lower()], stopwords)

    for match in re.finditer(r"분류:\s*([^.,\n]+)", text):
        category = match.group(1).strip()
        if category and category not in stopwords:
            _append_keywords(keywords, [category.lower()], stopwords)

    return keywords


def extract_keywords(
    text: str,
    max_keywords: int = 20,
    vocabulary: OrganizationKeywordVocabulary | None = None,
    *,
    for_query: bool = False,
) -> list[str]:
    """
    chunk/질문 텍스트에서 검색용 keyword를 추출한다.

    조직별 서비스명·상품명은 vocabulary(services/service_items/knowledge)에서
    불러오고, 코드에는 업종 공통 구조(가격/시간/마크다운 헤더)만 둔다.

    for_query=True면 질의용으로 동의어 전체 확장을 생략한다(하이브리드 RPC
    keyword_score 분모가 불필요하게 커지는 것을 막는다).
    """
    stopwords = set(_BASE_STOPWORDS)
    expand_synonyms = not for_query

    org_keywords = (
        _extract_vocabulary_keywords(
            text,
            vocabulary,
            stopwords,
            expand_synonyms=expand_synonyms,
        )
        if vocabulary
        else []
    )
    structured = _extract_structured_keywords(
        text,
        vocabulary,
        stopwords,
        expand_synonyms=expand_synonyms,
    )
    financial = _extract_financial_keywords(text, stopwords)

    english: list[str] = []
    for word in re.findall(r"[A-Za-z]{3,}", text):
        w = word.lower()
        if w not in {"nan", "none", "true", "false", "null", "the", "and", "for", "cad", "usd", "krw"}:
            english.append(w)

    korean: list[str] = []
    for word in re.findall(r"[가-힣]{2,}", text):
        if word in stopwords or len(word) > 12:
            continue
        if expand_synonyms:
            korean.extend(_normalize_korean_keyword(word, vocabulary))
        else:
            stripped = _strip_korean_token(word)
            if stripped and len(stripped) >= 2:
                korean.append(stripped.lower())

    numeric: list[str] = []
    for match in re.findall(
        r"\d+(?:,\d{3})*(?:\.\d+)?(?:원|분|평|개|명|시간|일|개월|cad|usd|krw)",
        text,
        re.IGNORECASE,
    ):
        numeric.append(match.lower())

    seen: set[str] = set()
    result: list[str] = []
    for word in org_keywords + structured + financial + english + korean + numeric:
        key = word.lower()
        if key in seen or key in stopwords:
            continue
        seen.add(key)
        result.append(key)
        if len(result) >= max_keywords:
            break

    if for_query:
        return collapse_query_keywords(result, vocabulary)
    return result


def collapse_query_keywords(
    keywords: list[str],
    vocabulary: OrganizationKeywordVocabulary | None = None,
) -> list[str]:
    """질의 keyword에서 동의어군·phrase partial 중복을 줄인다."""
    groups: list[frozenset[str]] = list(UNIVERSAL_SYNONYM_GROUPS)
    if vocabulary:
        groups.extend(vocabulary.synonym_groups)

    word_to_group: dict[str, int] = {}
    for idx, group in enumerate(groups):
        for word in group:
            word_to_group[word.lower()] = idx

    best_by_group: dict[int, str] = {}
    for keyword in keywords:
        key = keyword.lower().strip()
        if len(key) < 2:
            continue
        group_id = word_to_group.get(key)
        if group_id is None:
            continue
        current = best_by_group.get(group_id)
        if current is None or len(key) > len(current):
            best_by_group[group_id] = key

    seen_groups: set[int] = set()
    after_synonym: list[str] = []
    seen_ungrouped: set[str] = set()
    for keyword in keywords:
        key = keyword.lower().strip()
        if len(key) < 2:
            continue
        group_id = word_to_group.get(key)
        if group_id is not None:
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)
            after_synonym.append(best_by_group[group_id])
            continue
        if key in seen_ungrouped:
            continue
        seen_ungrouped.add(key)
        after_synonym.append(key)

    norms = {keyword: keyword.replace(" ", "") for keyword in after_synonym}
    drop: set[str] = set()
    kept_norms: list[str] = []
    for keyword in sorted(after_synonym, key=lambda item: len(norms[item]), reverse=True):
        normalized = norms[keyword]
        if any(
            normalized != kept and len(normalized) < len(kept) and normalized in kept
            for kept in kept_norms
        ):
            drop.add(keyword)
            continue
        if normalized in kept_norms:
            drop.add(keyword)
            continue
        kept_norms.append(normalized)

    return [keyword for keyword in after_synonym if keyword not in drop]


def prepare_keywords_for_hybrid_rpc(keywords: list[str]) -> list[str]:
    """
    hybrid RPC에 넘길 keyword 배열.
    공백 포함 phrase는 tsquery 오류를 막기 위해 붙여쓴 form만 추가한다.
    """
    existing = {keyword.strip().lower() for keyword in keywords if keyword.strip()}
    seen: set[str] = set()
    prepared: list[str] = []
    for keyword in keywords:
        normalized = keyword.strip().lower()
        if len(normalized) < 2:
            continue
        variants = [normalized]
        compact = normalized.replace(" ", "")
        if compact != normalized:
            variants.append(compact)
        elif " " not in normalized and len(normalized) >= 4:
            variants.append(compact)
        if " " in normalized:
            compact_variant = normalized.replace(" ", "")
            if compact_variant not in variants:
                variants.append(compact_variant)
        for variant in variants:
            if variant in seen:
                continue
            seen.add(variant)
            prepared.append(variant)
    return prepared


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
    pre_chunked: list[str] | None = None,
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
        chunks = pre_chunked if pre_chunked else chunk_text(text)

        update_source_status(source_id, "embedding")
        embeddings = create_embeddings_batch(chunks)
        vocabulary = get_organization_keyword_vocabulary(organization_id, force_reload=True)

        rows = []

        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            rows.append({
                "organization_id": organization_id,
                "source_id": source_id,
                "folder_id": folder_id,
                "chunk_index": index,
                "content": chunk,
                "embedding": embedding,
                "keywords": extract_keywords(chunk, vocabulary=vocabulary),
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
