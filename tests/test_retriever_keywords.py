from app.rag.keyword_vocabulary import OrganizationKeywordVocabulary
from app.rag.query_matching import keyword_hits_in_content


def test_keyword_hits_match_spaced_service_name_via_synonyms():
    content = "### 서비스 아이템: 입주 청소\n기본 가격: 180,000원"
    chunk_keywords = ["입주", "청소", "180,000원", "가격"]
    vocabulary = OrganizationKeywordVocabulary(
        organization_id="org",
        terms={"입주 청소", "입주청소"},
        synonym_groups=(frozenset({"가격", "비용", "얼마", "요금"}),),
    )
    keywords = ["입주청소", "얼마"]
    assert keyword_hits_in_content(
        content,
        keywords,
        chunk_keywords=chunk_keywords,
        vocabulary=vocabulary,
    ) >= 2
