def execute_end_session(*, farewell_message: str, on_delta=None) -> dict:
    """세션 종료 상태 반환. on_delta로 작별 인사 텍스트를 스트리밍한다."""
    message = farewell_message.strip() or "네, 감사합니다. 좋은 하루 되세요."
    if on_delta:
        on_delta(message)
    return {
        "answer": message,
        "intent": "end_session",
        "next_action": "end_session",
        "task_type": "none",
        "use_knowledge": False,
        "should_end_session": True,
        "final_response": message,
        "messages": [{"role": "assistant", "content": message}],
        "rules": [],
        "applied_rules": [],
    }
