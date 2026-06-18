from app.graph.state import AgentState


GREETING_KEYWORDS = [
    "안녕",
    "안녕하세요",
    "반가워",
    "반갑습니다",
    "고마워",
    "감사",
    "감사합니다",
]


RESERVATION_TIME_KEYWORDS = [
    "예약 가능한 시간",
    "예약 가능 시간",
    "가능한 시간",
    "빈 시간",
    "비어있는 시간",
    "예약 시간",
    "가장 빠른 시간",
    "일정",
]


KNOWLEDGE_SKIP_INTENTS = {
    "handoff",
}


def normalize_message(message: str) -> str:
    """
    공백을 제거해서 간단한 키워드 비교가 잘 되도록 정리한다.
    """
    return message.strip().replace(" ", "")


def contains_any_keyword(message: str, keywords: list[str]) -> bool:
    """
    메시지에 keywords 중 하나라도 포함되어 있는지 확인한다.
    """
    normalized_message = normalize_message(message)

    for keyword in keywords:
        normalized_keyword = normalize_message(keyword)

        if normalized_keyword in normalized_message:
            return True

    return False


def is_simple_greeting(message: str) -> bool:
    """
    단순 인사말인지 확인한다.

    예:
    - 안녕하세요
    - 반가워요
    - 감사합니다
    """
    normalized_message = normalize_message(message)

    if not normalized_message:
        return False

    return contains_any_keyword(
        message=message,
        keywords=GREETING_KEYWORDS,
    )


def is_reservation_time_question(message: str) -> bool:
    """
    예약 가능 시간 / 일정 조회 질문인지 확인한다.

    이런 질문은 Knowledge 문서가 아니라
    나중에 예약 DB나 캘린더 노드가 처리해야 한다.

    예:
    - 예약 가능한 시간 알려줘
    - 오늘 빈 시간 있어?
    - 가장 빠른 예약 시간 알려줘
    """
    return contains_any_keyword(
        message=message,
        keywords=RESERVATION_TIME_KEYWORDS,
    )


def should_use_knowledge(message: str, intent: str | None) -> bool:
    """
    Knowledge 검색이 필요한지 판단한다.

    중요한 방향:
    - Knowledge를 써야 하는 단어를 계속 하드코딩하지 않는다.
    - 명확히 Knowledge가 필요 없는 경우만 제외한다.
    - 나머지는 일단 Knowledge 검색을 시도한다.
    - 실제로 사용할지는 retriever의 유사도 threshold가 결정한다.

    예:
    - 안녕하세요 → False
    - 상담사 연결해주세요 → False
    - 예약 가능한 시간 알려줘 → False
    - 프리미엄 청소 가격이 얼마야? → True
    - 강아지 데리고 가도 돼요? → True
    - 주차 가능해요? → True
    """

    if is_simple_greeting(message):
        return False

    if is_reservation_time_question(message):
        return False

    if intent in KNOWLEDGE_SKIP_INTENTS:
        return False

    return True


def should_use_knowledge_node(state: AgentState) -> AgentState:
    """
    현재 사용자 메시지가 Knowledge 검색을 거칠지 결정한다.
    """

    message = state.get("user_message", "")
    intent = state.get("intent")

    use_knowledge = should_use_knowledge(
        message=message,
        intent=intent,
    )

    state["should_use_knowledge"] = use_knowledge

    if not use_knowledge:
        state["knowledge_context"] = []
        state["used_knowledge"] = []

    return state