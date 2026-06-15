from typing import TypedDict, Optional, Dict, Any, List


class AgentState(TypedDict):
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

    # Rule Node에서 불러온 전체 규칙
    rules: List[Dict[str, Any]]

    # 실제 적용된 rule 이름 목록
    applied_rules: List[str]

    # RAG 검색 결과 원본
    knowledge_context: List[Dict[str, Any]]

    # 관리자 로그용으로 정리된 사용 지식 목록
    used_knowledge: List[Dict[str, Any]]

    # 최종 AI 응답
    final_response: Optional[str]