from app.rag.retriever import summarize_knowledge_chunk


def test_summarize_service_description_sounds_natural():
    chunk = {
        "content": (
            "### 서비스 아이템: 화장실 청소\n"
            "설명: 화장실 바닥, 변기, 세면대, 거울을 청소하는 기본 서비스"
        )
    }
    answer = summarize_knowledge_chunk(chunk)
    assert answer is not None
    assert "화장실 청소:" not in answer
    assert answer.startswith("화장실 청소는")
    assert answer.endswith("예요.")


def test_summarize_move_in_cleaning_price_sounds_natural():
    chunk = {
        "content": (
            "### 서비스 아이템: 입주 청소\n"
            "기본 가격: 500000원\n"
            "설명: 입주 전 집 전체를 청소하는 서비스"
        )
    }
    answer = summarize_knowledge_chunk(chunk)
    assert answer is not None
    assert "입주 청소:" not in answer
    assert "입주 청소는" in answer


def test_summarize_price_only_chunk():
    chunk = {
        "content": "### 서비스 아이템: 입주 청소\n기본 가격: 500000원",
    }
    answer = summarize_knowledge_chunk(chunk)
    assert answer == "입주 청소는 기본 가격이 500000원이에요."
