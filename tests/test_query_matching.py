from app.rag.query_matching import looks_like_question


def test_looks_like_question_detects_question_mark():
    assert looks_like_question("입주청소얼마에요?") is True
    assert looks_like_question("입주청소") is False
