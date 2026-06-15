def build_rules_text(applied_rules: list[str]) -> str:
    """
    적용된 규칙 목록을 AI instruction에 넣기 좋은 텍스트로 변환한다.
    """

    if not applied_rules:
        return "적용된 규칙이 없습니다."

    return "\n".join([f"- {rule}" for rule in applied_rules])


def build_knowledge_text(knowledge_context: list[dict]) -> str:
    """
    RAG로 검색된 지식 chunk 목록을 AI instruction용 텍스트로 변환한다.
    """

    if not knowledge_context:
        return "현재 참고할 수 있는 등록 지식이 없습니다."

    lines = []

    for index, item in enumerate(knowledge_context, start=1):
        source_title = item.get("source_title") or "Unknown source"
        content = item.get("content") or ""

        lines.append(
            f"[지식 {index}]\n"
            f"출처: {source_title}\n"
            f"내용: {content}"
        )

    return "\n\n".join(lines)


def build_session_context_text(session_state: dict | None) -> str:
    """
    Redis에 저장된 이전 턴의 session_state를 AI instruction용 텍스트로 변환한다.

    이전 응답, 이전 intent, 진행 중인 task(예: 예약) 등을
    AI가 멀티턴 대화 맥락으로 참고할 수 있게 한다.
    """

    if not session_state:
        return "이전 대화 맥락이 없습니다."

    lines = []

    last_intent = session_state.get("last_intent")
    last_user_message = session_state.get("last_user_message")
    last_response = session_state.get("last_response")
    active_task = session_state.get("active_task")
    step = session_state.get("step")

    if last_user_message:
        lines.append(f"이전 고객 메시지: {last_user_message}")

    if last_response:
        lines.append(f"이전 AI 응답: {last_response}")

    if last_intent:
        lines.append(f"이전 intent: {last_intent}")

    if active_task:
        lines.append(f"진행 중인 task: {active_task} (step: {step})")

    if not lines:
        return "이전 대화 맥락이 없습니다."

    return "\n".join(lines)


def build_response_instructions(
    intent: str | None,
    applied_rules: list[str],
    knowledge_context: list[dict],
    session_state: dict | None = None,
) -> str:
    """
    AI 답변 생성을 위한 instructions를 만든다.

    일반 /chat 응답과 WebSocket streaming 응답에서 공통으로 사용한다.
    """

    rules_text = build_rules_text(applied_rules)
    knowledge_text = build_knowledge_text(knowledge_context)
    session_context_text = build_session_context_text(session_state)

    return f"""
너는 Front Agent의 AI 상담사다.

역할:
- 고객의 질문에 친절하고 간결하게 답변한다.
- 등록된 지식과 규칙을 우선으로 따른다.
- 확실하지 않은 내용은 지어내지 않는다.
- 지식에 없는 정보는 담당자 확인이 필요하다고 안내한다.

현재 intent:
{intent}

이전 대화 맥락:
{session_context_text}

반드시 지켜야 할 규칙:
{rules_text}

참고 가능한 지식:
{knowledge_text}

응답 원칙:
- 한국어로 답변한다.
- 너무 길게 설명하지 않는다.
- 가격, 예약, 일정 같은 정보는 등록된 지식에 있을 때만 확정적으로 말한다.
- 예약 생성 태스크가 실제로 성공하기 전에는 예약 완료라고 말하지 않는다.
- 이전 대화 맥락을 참고해 자연스럽게 이어서 답변하되, 이전 맥락과 다른 새 질문이면 새 질문을 우선한다.
""".strip()