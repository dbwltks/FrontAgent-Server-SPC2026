from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.db import supabase

# 업종 공통으로 자주 쓰이는 질의 표현만 코드에 둔다. 구체 서비스명·상품명은
# 조직의 services / service_items / knowledge_sources에서 불러온다.
UNIVERSAL_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"가격", "요금", "비용", "얼마"}),
    frozenset({"예약", "예약하기"}),
    frozenset({"취소", "예약취소", "당일취소"}),
    frozenset({"상담", "상담원", "상담연결"}),
    frozenset({"영업시간", "운영시간", "운영", "몇시"}),
)


@dataclass
class OrganizationKeywordVocabulary:
    organization_id: str
    terms: set[str] = field(default_factory=set)
    synonym_groups: tuple[frozenset[str], ...] = ()

    def expand(self, word: str) -> list[str]:
        normalized = word.strip().lower()
        if not normalized:
            return []
        for group in self.synonym_groups:
            if normalized in group:
                return sorted(group)
        return [normalized]


_vocab_cache: dict[str, OrganizationKeywordVocabulary] = {}


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _phrase_variants(phrase: str) -> set[str]:
    normalized = _normalize_spaces(phrase).lower()
    if len(normalized) < 2:
        return set()

    variants = {normalized}
    if " " in normalized:
        variants.add(normalized.replace(" ", ""))
        for part in normalized.split():
            if len(part) >= 2:
                variants.add(part)
    return variants


def _collect_terms(raw_terms: list[str]) -> set[str]:
    terms: set[str] = set()
    for raw in raw_terms:
        text = _normalize_spaces(str(raw or ""))
        if len(text) < 2:
            continue
        terms.update(_phrase_variants(text))
    return terms


def _build_synonym_groups(raw_terms: list[str]) -> tuple[frozenset[str], ...]:
    groups: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()

    for raw in raw_terms:
        text = _normalize_spaces(str(raw or ""))
        if len(text) < 2:
            continue
        group = frozenset(_phrase_variants(text))
        if len(group) < 2 or group in seen:
            continue
        seen.add(group)
        groups.append(group)

    return tuple(groups)


def load_organization_keyword_vocabulary(organization_id: str) -> OrganizationKeywordVocabulary:
    raw_terms: list[str] = []

    services = (
        supabase.table("services")
        .select("name, description")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .eq("approval_status", "approved")
        .execute()
    )
    for row in services.data or []:
        raw_terms.append(row.get("name") or "")
        raw_terms.append(row.get("description") or "")

    items = (
        supabase.table("service_items")
        .select("name, description")
        .eq("organization_id", organization_id)
        .eq("is_available", True)
        .execute()
    )
    for row in items.data or []:
        raw_terms.append(row.get("name") or "")
        raw_terms.append(row.get("description") or "")

    options = (
        supabase.table("service_item_options")
        .select("option_group, option_value")
        .eq("organization_id", organization_id)
        .eq("is_available", True)
        .execute()
    )
    for row in options.data or []:
        raw_terms.append(row.get("option_group") or "")
        raw_terms.append(row.get("option_value") or "")

    sources = (
        supabase.table("knowledge_sources")
        .select("title")
        .eq("organization_id", organization_id)
        .eq("is_referenced", True)
        .execute()
    )
    for row in sources.data or []:
        raw_terms.append(row.get("title") or "")

    org_groups = _build_synonym_groups(raw_terms)
    all_groups = org_groups + UNIVERSAL_SYNONYM_GROUPS

    terms = _collect_terms(raw_terms)
    for group in all_groups:
        terms.update(group)

    return OrganizationKeywordVocabulary(
        organization_id=organization_id,
        terms=terms,
        synonym_groups=all_groups,
    )


def get_organization_keyword_vocabulary(
    organization_id: str,
    *,
    force_reload: bool = False,
) -> OrganizationKeywordVocabulary:
    if not force_reload and organization_id in _vocab_cache:
        return _vocab_cache[organization_id]

    vocabulary = load_organization_keyword_vocabulary(organization_id)
    _vocab_cache[organization_id] = vocabulary
    return vocabulary


def clear_organization_keyword_vocabulary(organization_id: str) -> None:
    _vocab_cache.pop(organization_id, None)
