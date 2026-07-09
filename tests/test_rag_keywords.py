from unittest.mock import patch

from app.graph.handlers.agent_node import _build_knowledge_search_query
from app.rag.indexer import (
    collapse_query_keywords,
    extract_keywords,
    prepare_keywords_for_hybrid_rpc,
)
from app.rag.keyword_vocabulary import OrganizationKeywordVocabulary
from app.rag.query_matching import term_appears_in_text


def test_vocabulary_matches_compact_service_name():
    vocabulary = OrganizationKeywordVocabulary(
        organization_id="org",
        terms={"입주 청소", "입주청소", "화장실 청소"},
        synonym_groups=(),
    )
    assert term_appears_in_text("입주 청소", "입주청소얼마에요?") is True
    keywords = extract_keywords(
        "입주청소얼마에요?",
        vocabulary=vocabulary,
        for_query=True,
        max_keywords=10,
    )
    assert "입주 청소" in keywords


def test_query_keywords_strip_얼마에요_suffix():
    keywords = extract_keywords("화장실청소얼마에요?", for_query=True)
    assert "화장실청소" in keywords
    assert "화장실청소얼마에요" not in keywords


def test_query_keywords_skip_synonym_expansion():
    keywords = extract_keywords("영업시간이 언제예요?", for_query=True)
    assert "영업시간" in keywords
    assert "운영시간" not in keywords
    assert "몇시" not in keywords


def test_query_keywords_collapse_price_synonyms():
    vocabulary = OrganizationKeywordVocabulary(
        organization_id="org",
        terms={"얼마", "가격", "비용", "요금"},
        synonym_groups=(frozenset({"가격", "비용", "얼마", "요금"}),),
    )
    keywords = extract_keywords(
        "근데 얼마예요?",
        vocabulary=vocabulary,
        for_query=True,
    )
    price_terms = {"가격", "비용", "얼마", "요금"}
    assert len(price_terms.intersection(keywords)) == 1


def test_prepare_keywords_only_adds_compact_phrase_variant():
    keywords = ["화장실 청소", "가격"]
    prepared = prepare_keywords_for_hybrid_rpc(keywords)
    assert prepared == ["화장실 청소", "화장실청소", "가격"]


def test_collapse_query_keywords_drops_phrase_parts():
    vocabulary = OrganizationKeywordVocabulary(
        organization_id="org",
        terms={"화장실 청소", "화장실", "청소"},
        synonym_groups=(frozenset({"화장실 청소", "화장실청소", "화장실", "청소"}),),
    )
    collapsed = collapse_query_keywords(
        ["청소", "화장실", "화장실 청소", "화장실청소"],
        vocabulary,
    )
    assert collapsed == ["화장실 청소"]


@patch("app.graph.handlers.agent_node.resolve_task_variables")
def test_build_knowledge_search_query_dedupes_service_variables(mock_resolve):
    mock_resolve.return_value = {
        "service_item_name": "화장실 청소",
        "service_name": "화장실 청소",
        "available_services": {
            "services": [{"name": "화장실 청소"}, {"name": "베란다 청소"}]
        },
    }
    query = _build_knowledge_search_query("가격 알려주세요", "org", "session")
    assert query.count("화장실 청소") == 1
