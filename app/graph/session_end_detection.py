import re

# 상담(채팅·통화) 종료 의도가 분명한 표현. 애매한 경우는 decision_node LLM에 맡긴다.
_OBVIOUS_END_SESSION_PATTERNS = (
    "통화종료",
    "통화끊",
    "전화끊",
    "전화종료",
    "대화종료",
    "채팅종료",
    "상담종료",
    "대화끝",
    "채팅끝",
    "상담끝",
    "끊어줘",
    "끊어주세요",
    "끊을게",
    "끊을게요",
    "통화그만",
    "전화그만",
    "그만끊",
    "전화끊어",
    "통화끊어",
    "여기까지",
    "이만할게",
    "그만할게",
)


def is_obvious_end_session_request(message: str) -> bool:
    normalized = re.sub(r"\s+", "", (message or "").strip().lower())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in _OBVIOUS_END_SESSION_PATTERNS)


# 상담원(사람) 연결 의도가 분명한 표현. 예약 진행 중 agent LLM 생략 fast-path에서 사용한다.
_OBVIOUS_HANDOFF_PATTERNS = (
    "상담원연결",
    "상담원바꿔",
    "상담원불러",
    "상담원하고",
    "사람연결",
    "사람불러",
    "사람이랑",
    "사람하고",
    "직원연결",
    "직원불러",
    "직원바꿔",
    "담당자연결",
    "담당자불러",
    "사람한테연결",
    "상담원한테",
)


def is_obvious_handoff_request(message: str) -> bool:
    normalized = re.sub(r"\s+", "", (message or "").strip().lower())
    if not normalized:
        return False
    return any(pattern in normalized for pattern in _OBVIOUS_HANDOFF_PATTERNS)


# 예약·지식·정책 의도가 섞이면 LLM/tool 경로를 탄다.
_TASK_OR_KNOWLEDGE_HINT = re.compile(
    r"(예약|변경|취소|환불|얼마|가격|비용|요금|서비스|메뉴|가능|되나|주차|포함|"
    r"시간|정책|취소|영업|운영|상담원|직원|연결|\?|뭐|무엇|어떤|알려)",
    re.IGNORECASE,
)

_GREETING = re.compile(
    r"^(?:안녕(?:하세요|히세요|하십니까|)?|하이|헬로+|hello+|hi+|"
    r"good\s*morning|반가|반갑)",
    re.IGNORECASE,
)

_THANKS = re.compile(
    r"^(?:감사(?:합니다|해요|드)?|고마(?:워|워요|습니다)|고맙|thank)",
    re.IGNORECASE,
)

_SHORT_ACK = re.compile(
    r"^(?:네|예|응|알겠(?:습니다|어요|습)?|ok|okay)$",
    re.IGNORECASE,
)


def _looks_like_task_or_knowledge_intent(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return True
    return bool(_TASK_OR_KNOWLEDGE_HINT.search(text))


def try_general_fast_path_response(
    message: str,
    *,
    has_prior_assistant_turn: bool = False,
) -> str | None:
    """인사·감사·짧은 호응만 LLM 없이 즉시 응답. None이면 agent LLM 경로."""
    raw = (message or "").strip()
    if not raw or len(raw) > 40:
        return None
    if _looks_like_task_or_knowledge_intent(raw):
        return None

    normalized = re.sub(r"\s+", "", raw.lower())

    if _GREETING.search(normalized):
        if has_prior_assistant_turn:
            return "네, 말씀해 주세요. 무엇을 도와드릴까요?"
        return "안녕하세요! 무엇을 도와드릴까요?"

    if _THANKS.search(normalized):
        return "네, 도움이 필요하시면 언제든 말씀해 주세요."

    if len(normalized) <= 8 and _SHORT_ACK.fullmatch(normalized):
        return "네, 알겠습니다."

    return None
