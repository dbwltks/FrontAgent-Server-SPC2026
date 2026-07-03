from unittest.mock import patch

from app.graph.nodes.agent_node import (
    _build_knowledge_search_query,
    _extract_available_service_names,
    _looks_like_knowledge_interrupt,
    _looks_like_task_slot_answer,
    _task_turn_made_progress,
)

ASK_SERVICE_PROMPT = (
    "어떤 서비스를 원하시나요? "
    "화장실 청소, 베란다 청소, 입주 청소, 주방 청소 중에서 선택해 주세요."
)


def test_service_selection_answer_is_not_faq_interrupt():
    message = "저 화장실청소요"
    assert _looks_like_task_slot_answer(message, ASK_SERVICE_PROMPT) is True
    assert _looks_like_knowledge_interrupt(message, ASK_SERVICE_PROMPT) is False


def test_service_selection_short_answer_is_not_faq_interrupt():
    message = "베란다 청소"
    assert _looks_like_task_slot_answer(message, ASK_SERVICE_PROMPT) is True
    assert _looks_like_knowledge_interrupt(message, ASK_SERVICE_PROMPT) is False


def test_duration_question_is_faq_interrupt():
    message = "시간 얼마나 걸려요?"
    assert _looks_like_knowledge_interrupt(message, ASK_SERVICE_PROMPT) is True


def test_price_question_is_faq_interrupt():
    message = "근데 얼마예요?"
    assert _looks_like_knowledge_interrupt(message, ASK_SERVICE_PROMPT) is True


def test_task_progress_when_service_resolved():
    assert _task_turn_made_progress(
        before_step="ask_service",
        before_vars={},
        after_step="ask_name",
        after_vars={"service_item_id": "svc-1"},
        task_status="waiting_user_input",
    )


def test_task_no_progress_when_stuck_on_same_step():
    assert not _task_turn_made_progress(
        before_step="ask_service",
        before_vars={},
        after_step="ask_service",
        after_vars={"service_item_text": "근데 얼마예요?"},
        task_status="waiting_user_input",
    )


def test_extract_available_service_names():
    names = _extract_available_service_names(
        {
            "available_services": {
                "services": [
                    {"name": "화장실 청소"},
                    {"name": "베란다 청소"},
                ]
            }
        }
    )
    assert names == ["화장실 청소", "베란다 청소"]


@patch("app.graph.nodes.agent_node.resolve_task_variables")
def test_build_knowledge_search_query_uses_available_services(mock_resolve):
    mock_resolve.return_value = {
        "available_services": {
            "services": [
                {"name": "화장실 청소"},
                {"name": "베란다 청소"},
            ]
        }
    }
    query = _build_knowledge_search_query("근데 얼마예요?", "org", "session")
    assert "화장실 청소" in query
    assert "베란다 청소" in query
    assert "가격" in query


@patch("app.graph.nodes.agent_node.resolve_task_variables")
def test_build_knowledge_search_query_skips_catalog_when_service_named(mock_resolve):
    mock_resolve.return_value = {
        "service_item_name": "화장실 청소",
        "available_services": {
            "services": [{"name": "화장실 청소"}, {"name": "베란다 청소"}]
        },
    }
    query = _build_knowledge_search_query("화장실 청소 가격", "org", "session")
    assert query.count("화장실 청소") == 1
    assert "베란다 청소" not in query
