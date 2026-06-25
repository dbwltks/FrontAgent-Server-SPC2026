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
