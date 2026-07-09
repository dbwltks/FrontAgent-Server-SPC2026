def execute_handoff() -> dict:
    """상담원 연결 요청 상태 반환."""
    return {
        "answer": None,
        "intent": "handoff",
        "next_action": "handoff",
        "task_type": "none",
        "use_knowledge": False,
        "should_end_session": False,
        "final_response": None,
        "rules": [],
        "applied_rules": [],
    }
