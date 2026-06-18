from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict):
    # 요청 기본 정보
    organization_id: str
    session_id: str
    user_message: str

    # 현재 상담방 ID
    conversation_id: Optional[str]

    # 현재 상담방의 AI 자동응답 여부
    ai_enabled: bool

    # Redis에서 불러온 세션 상태
    session_state: Dict[str, Any]

    # Decision Node에서 분류한 intent
    # 예: pricing, reservation, handoff, faq, general
    intent: Optional[str]

    # Decision Node에서 결정한 다음 행동
    # 예: search_knowledge, run_task, handoff, respond_general
    next_action: Optional[str]

    # 실행할 태스크 종류
    # 예: reservation_create, reservation_lookup, reservation_cancel, none
    task_type: Optional[str]

    # Knowledge 검색 여부
    use_knowledge: bool

    # Decision Node가 그렇게 판단한 이유
    decision_reason: Optional[str]

    # 나중에 task_node 실행 결과를 저장할 공간
    task_result: Optional[Dict[str, Any]]

    # Rule Node에서 불러온 활성 규칙 목록
    # 예:
    # [
    #   {
    #     "id": "...",
    #     "name": "반말하지 않기",
    #     "instruction": "고객에게 절대 반말하지 않고 항상 존댓말로 응답한다."
    #   }
    # ]
    rules: List[Dict[str, Any]]

    # AI 프롬프트에 넣기 좋게 정리된 규칙 지시문 문자열
    # 예:
    # [응답 규칙]
    # 1. 반말하지 않기
    # - 고객에게 절대 반말하지 않고 항상 존댓말로 응답한다.
    rule_instructions: str

    # 관리자 로그용으로 정리된 적용 규칙 이름 목록
    applied_rules: List[str]

    # RAG 검색 결과 원본
    knowledge_context: List[Dict[str, Any]]

    # 관리자 로그용으로 정리된 사용 지식 목록
    used_knowledge: List[Dict[str, Any]]

    # 최종 AI 응답
    final_response: Optional[str]