"""질의·키워드 매칭 공통 유틸 (조직 vocabulary / chunk keywords 기반)."""

from __future__ import annotations

import re

from app.rag.keyword_vocabulary import (
    UNIVERSAL_SYNONYM_GROUPS,
    OrganizationKeywordVocabulary,
)

KOREAN_QUESTION_SUFFIX = re.compile(
    r"(얼마야|얼마예요|얼마에요|얼마인가요|얼마|인가요|인가|뭐야|뭐예요|뭐에요|무엇|어때|할까요|할까|인지)$"
)


def compact_label(text: str) -> str:
    return re.sub(r"[\s?!.,·]+", "", (text or "").strip().lower())


def term_appears_in_text(term: str, text: str) -> bool:
    lowered = text.lower()
    normalized = term.lower().strip()
    if not normalized:
        return False
    if normalized in lowered:
        return True
    compact_term = compact_label(normalized)
    if len(compact_term) < 4:
        return False
    return compact_term in compact_label(text)


def looks_like_question(message: str) -> bool:
    """물음표 또는 한국어 의문형 어미 — 업종/서비스명 하드코딩 없음."""
    text = message.strip()
    if not text:
        return False
    if "?" in text or "？" in text:
        return True
    for token in re.findall(r"[가-힣]{2,}", text):
        if KOREAN_QUESTION_SUFFIX.search(token):
            return True
    return bool(re.search(r"(알려\d*|궁금)", text))


def expand_query_keyword_variants(
    keyword: str,
    vocabulary: OrganizationKeywordVocabulary | None = None,
) -> set[str]:
    normalized = keyword.strip().lower()
    if len(normalized) < 2:
        return set()

    variants = {normalized, compact_label(normalized)}
    groups = list(UNIVERSAL_SYNONYM_GROUPS)
    if vocabulary:
        groups.extend(vocabulary.synonym_groups)

    for group in groups:
        if normalized in group or compact_label(normalized) in {compact_label(w) for w in group}:
            variants.update(word.lower() for word in group)
            variants.update(compact_label(word) for word in group)

    return {variant for variant in variants if len(variant) >= 2}


def keyword_hits_in_content(
    content: str,
    query_keywords: list[str],
    *,
    chunk_keywords: list[str] | None = None,
    vocabulary: OrganizationKeywordVocabulary | None = None,
) -> int:
    """query keyword(동의어·띄어쓰기 변형 포함)가 content/chunk keywords에 있는지 센다."""
    lowered = content.lower()
    collapsed_content = compact_label(content)
    normalized_chunk_keywords = {
        kw.lower()
        for kw in (chunk_keywords or [])
        if kw and len(str(kw)) >= 2
    }
    compact_chunk_keywords = {compact_label(kw) for kw in normalized_chunk_keywords}

    hits = 0
    for keyword in query_keywords:
        if len(keyword) < 2:
            continue
        matched = False
        for variant in expand_query_keyword_variants(keyword, vocabulary):
            if (
                variant in lowered
                or variant in collapsed_content
                or variant in normalized_chunk_keywords
                or variant in compact_chunk_keywords
            ):
                matched = True
                break
        if matched:
            hits += 1
    return hits
