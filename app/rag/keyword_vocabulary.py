from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.core.db import supabase
from app.core.redis import redis_client

_VOCAB_REDIS_TTL = 60 * 10  # 10분

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
    # name/option_value 등 짧은 용어는 단어 단위 분리(phrase_variants)로 동의어 생성.
    # description처럼 긴 문장은 통째로만 terms에 추가한다 - 단어 단위로 쪼개면
    # "1회,", "격주", "공간을" 같은 의미 없는 조각이 vocabulary에 들어가 keyword
    # 추출을 오염시킨다(실측: 이사 청소/베란다 청소 청크 keywords가 정기청소
    # description 단어들로 채워져 "이사" keyword가 max_keywords에 못 들어가는 사례).
    name_terms: list[str] = []   # phrase_variants 적용 (단어 분리 O)
    phrase_only_terms: list[str] = []  # 통째로만 (단어 분리 X)

    services = (
        supabase.table("services")
        .select("name, description")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .eq("approval_status", "approved")
        .execute()
    )
    for row in services.data or []:
        name_terms.append(row.get("name") or "")
        phrase_only_terms.append(row.get("description") or "")

    items = (
        supabase.table("service_items")
        .select("name, description")
        .eq("organization_id", organization_id)
        .eq("is_available", True)
        .execute()
    )
    for row in items.data or []:
        name_terms.append(row.get("name") or "")
        phrase_only_terms.append(row.get("description") or "")

    options = (
        supabase.table("service_item_options")
        .select("option_group, option_value")
        .eq("organization_id", organization_id)
        .eq("is_available", True)
        .execute()
    )
    for row in options.data or []:
        name_terms.append(row.get("option_group") or "")
        name_terms.append(row.get("option_value") or "")

    sources = (
        supabase.table("knowledge_sources")
        .select("title")
        .eq("organization_id", organization_id)
        .eq("is_referenced", True)
        .execute()
    )
    for row in sources.data or []:
        # 파일명(01_서비스_전체_카탈로그.md)은 vocabulary 노이즈만 유발하므로 제외
        pass

    # synonym group과 phrase_variants는 name(짧은 용어)에만 적용한다.
    # description(긴 문장)을 _build_synonym_groups에 넣으면 문장 안 개별 단어들이
    # 모두 synonym group에 들어가고, terms.update(group)으로 vocabulary가 오염된다.
    org_groups = _build_synonym_groups(name_terms)
    all_groups = org_groups + UNIVERSAL_SYNONYM_GROUPS

    terms = _collect_terms(name_terms)
    # description/긴 설명은 통째로만 추가 (단어 분리 없이)
    for raw in phrase_only_terms:
        text = _normalize_spaces(str(raw or ""))
        if len(text) >= 2:
            terms.add(text.lower())

    for group in all_groups:
        terms.update(group)

    return OrganizationKeywordVocabulary(
        organization_id=organization_id,
        terms=terms,
        synonym_groups=all_groups,
    )


def _vocab_redis_key(organization_id: str) -> str:
    return f"rag_vocab:{organization_id}"


def _vocab_to_json(vocab: OrganizationKeywordVocabulary) -> str:
    return json.dumps({
        "terms": list(vocab.terms),
        "synonym_groups": [list(g) for g in vocab.synonym_groups],
    })


def _vocab_from_json(organization_id: str, raw: str) -> OrganizationKeywordVocabulary:
    data = json.loads(raw)
    return OrganizationKeywordVocabulary(
        organization_id=organization_id,
        terms=set(data["terms"]),
        synonym_groups=tuple(frozenset(g) for g in data["synonym_groups"]),
    )


def get_organization_keyword_vocabulary(
    organization_id: str,
    *,
    force_reload: bool = False,
) -> OrganizationKeywordVocabulary:
    if not force_reload and organization_id in _vocab_cache:
        return _vocab_cache[organization_id]

    # Redis 캐시 확인 (프로세스 재시작 후에도 빠르게)
    if not force_reload:
        try:
            raw = redis_client.get(_vocab_redis_key(organization_id))
            if raw:
                vocab = _vocab_from_json(organization_id, raw)
                _vocab_cache[organization_id] = vocab
                return vocab
        except Exception:
            pass

    vocabulary = load_organization_keyword_vocabulary(organization_id)
    _vocab_cache[organization_id] = vocabulary

    try:
        redis_client.setex(_vocab_redis_key(organization_id), _VOCAB_REDIS_TTL, _vocab_to_json(vocabulary))
    except Exception:
        pass

    return vocabulary


def clear_organization_keyword_vocabulary(organization_id: str) -> None:
    _vocab_cache.pop(organization_id, None)
    try:
        redis_client.delete(_vocab_redis_key(organization_id))
    except Exception:
        pass
