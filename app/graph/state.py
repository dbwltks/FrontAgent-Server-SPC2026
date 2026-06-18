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

    # Router Node에서 분류한 intent
    intent: Optional[str]

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

    # 현재 메시지가 Knowledge/RAG 검색을 필요로 하는지 여부
    should_use_knowledge: bool

    # RAG 검색 결과 원본
    knowledge_context: List[Dict[str, Any]]

    # 관리자 로그용으로 정리된 사용 지식 목록
    used_knowledge: List[Dict[str, Any]]

    # 최종 AI 응답
    final_response: Optional[str]