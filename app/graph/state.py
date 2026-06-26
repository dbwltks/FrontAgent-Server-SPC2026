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

    # 진행 중 task_session 여부. conversation_node(병렬 브랜치)가 미리 조회해
    # join 라우팅이 동기 DB 호출 없이 분기하도록 한다.
    has_active_task: bool

    # 멀티턴 대화 히스토리. checkpointer가 thread_id(=organization_id:session_id) 기준으로
    # 자동 저장/복원한다. add_messages reducer가 매 턴 새 메시지를 누적시켜준다.
    messages: Annotated[list, add_messages]

    # 대화 히스토리로 표현되지 않는 구조화된 상태 (예약 진행 단계 등).
    # checkpointer가 함께 영속화한다.
    active_task: Optional[str]
    task_step: Optional[str]


    # 진행 중 태스크의 상세 컨텍스트
    # task_router_node가 "태스크 입력값인지 / 지식 질문인지 / 예약 가능 시간 질문인지" 판단할 때 사용한다.
    active_task_session: Optional[Dict[str, Any]]
    current_task_node: Optional[Dict[str, Any]]
    current_task_flow_id: Optional[str]
    current_task_node_key: Optional[str]
    current_task_node_type: Optional[str]
    pending_task_prompt: Optional[str]

    # 태스크 진행 중 전용 LLM router 결과
    # 예: continue_task, search_knowledge, check_availability, handoff, need_clarification
    task_route: Optional[str]
    task_route_confidence: Optional[float]
    task_route_reason: Optional[str]

    
    # Decision Node에서 분류한 intent
    # 예: pricing, reservation, handoff, faq, general, end_session
    intent: Optional[str]

    # Decision Node에서 결정한 다음 행동
    # 예: search_knowledge, run_task, handoff, respond_general, end_session
    next_action: Optional[str]

    # 상담 종료 의도(채팅·통화 공통). session_end 신호·DB closed 기록에 사용.
    should_end_session: bool

    # 실행할 태스크 종류
    # 예: reservation_create, reservation_lookup, reservation_cancel, none
    task_type: Optional[str]

    # Knowledge 검색 여부
    use_knowledge: bool

    # Decision Node가 그렇게 판단한 이유
    decision_reason: Optional[str]

    # 나중에 task_node 실행 결과를 저장할 공간
    task_result: Optional[Dict[str, Any]]

    task_handled: bool
    
    task_status: Optional[str]

    # 기존 should_use_knowledge_node와의 호환용
    # decision_node 구조가 안정화되면 나중에 제거 가능
    should_use_knowledge: bool

    # Rule Node에서 불러온 활성 규칙 목록
    rules: List[Dict[str, Any]]

    # 관리자 로그용으로 정리된 적용 규칙 이름 목록
    applied_rules: List[str]

    # 질문 분해 결과
    knowledge_queries: List[str]

    # 질문별 RAG 검색 결과
    knowledge_context_groups: List[Dict[str, Any]]
    # 예:
    # [
    #   {
    #     "query": "강아지 데려가도 돼",
    #     "chunks": [...]
    #   },
    #   {
    #     "query": "프리미엄 청소 얼마야",
    #     "chunks": [...]
    #   }
    # ]

    # RAG 검색 결과 원본
    knowledge_context: List[Dict[str, Any]]

    # 지식 폴더 제한 검색용
    knowledge_folder_id: Optional[str]

    # 관리자 로그용으로 정리된 사용 지식 목록
    used_knowledge: List[Dict[str, Any]]

    # 최종 AI 응답
    final_response: Optional[str]
