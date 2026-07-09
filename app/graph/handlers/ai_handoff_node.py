def build_ai_handoff_update(base_update: dict) -> dict:
    """
    상담방의 ai_enabled가 꺼져 있을 때 AI 응답 생성을 건너뛰고
    관리자 응답 대기 상태로 표시한다.
    """
    return {
        **base_update,
        "intent": "handoff",
        "next_action": "handoff",
        "task_type": "none",
        "use_knowledge": False,
        "decision_reason": "AI 자동응답이 꺼져 있어 관리자 응답 대기 상태로 전환",
        "task_result": None,
        "final_response": None,
        "rules": [],
        "applied_rules": [],
        "knowledge_context": [],
        "used_knowledge": [],
    }
