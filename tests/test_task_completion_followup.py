from app.graph.nodes.agent_node import (
    _append_task_completion_follow_up,
    _is_no_more_after_task_message,
    _task_follow_up_farewell,
)


def test_appends_chat_task_completion_follow_up():
    message = _append_task_completion_follow_up("예약 요청이 접수되었습니다.", "web_chat")

    assert "예약 요청이 접수되었습니다." in message
    assert "추가로 궁금한 점 있으실까요?" in message
    assert "여기서 마무리" in message


def test_appends_voice_task_completion_follow_up():
    message = _append_task_completion_follow_up("예약 요청이 접수되었습니다.", "web_call")

    assert "추가로 궁금한 점 있으실까요?" in message
    assert "통화를 종료" in message


def test_detects_no_more_after_task_messages():
    assert _is_no_more_after_task_message("없어요")
    assert _is_no_more_after_task_message("네 괜찮아요")
    assert _is_no_more_after_task_message("궁금한 거 없어요")


def test_does_not_treat_new_question_as_no_more():
    assert not _is_no_more_after_task_message("예약 변경도 가능한가요?")
    assert not _is_no_more_after_task_message("가격이 궁금해요")


def test_voice_task_follow_up_farewell_mentions_call_end():
    assert "통화 종료" in _task_follow_up_farewell("web_call")
