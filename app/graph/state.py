from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # 요청 기본 정보
    organization_id: str
    session_id: str
    user_message: str
    log_message: Optional[str]
    channel: str

    # 현재 상담방 ID
    conversation_id: Optional[str]

    # 현재 상담방의 AI 자동응답 여부
    ai_enabled: bool

    # 멀티턴 대화 히스토리
    messages: Annotated[list, add_messages]

    # 예약 태스크 진행 상태 (checkpointer가 영속화)
    active_task: Optional[str]
    task_step: Optional[str]
    task_result: Optional[Dict[str, Any]]
    task_status: Optional[str]

    # agent_node 결과
    intent: Optional[str]
    next_action: Optional[str]
    task_type: Optional[str]
    use_knowledge: bool
    decision_reason: Optional[str]
    should_end_session: bool

    # 응답
    final_response: Optional[str]
    follow_up_response: Optional[str]

    # rules
    rules: List[Dict[str, Any]]
    applied_rules: List[str]

    # knowledge
    knowledge_folder_id: Optional[str]
    knowledge_context: List[Dict[str, Any]]
    used_knowledge: List[Dict[str, Any]]
