def build_rule_instructions_text(rules: list[dict] | None) -> str:
    """
    DB에서 가져온 rules 목록을 AI가 참고하기 좋은 지시문 텍스트로 변환한다.

    이번 rules 구조에서는 필터/트리거/액션을 사용하지 않는다.
    오직 규칙 이름과 지시문만 프롬프트에 넣는다.
    """

    if not rules:
        return "등록된 응답 규칙이 없습니다."

    lines = ["[응답 규칙]"]

    for index, rule in enumerate(rules, start=1):
        name = rule.get("name") or f"규칙 {index}"
        instruction = rule.get("instruction") or ""

        if not instruction:
            continue

        lines.append(
            f"{index}. {name}\n"
            f"- {instruction}"
        )

    if len(lines) == 1:
        return "등록된 응답 규칙이 없습니다."

    return "\n\n".join(lines)


def build_rules_text(applied_rules: list[str]) -> str:
    """
    기존 코드와의 호환을 위해 남겨둔다.

    예전에는 applied_rules에 규칙 이름만 들어갔다.
    앞으로는 build_rule_instructions_text()를 우선 사용한다.
    """

    if not applied_rules:
        return "등록된 응답 규칙이 없습니다."

    return "\n".join(
        [
            f"- {rule}"
            for rule in applied_rules
        ]
    )


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


def build_knowledge_groups_text(knowledge_context_groups: list[dict] | None) -> str:
    """
    질문별 RAG 검색 결과를 AI instruction용 텍스트로 변환한다.
    사용자가 여러 질문을 한 경우, 어떤 지식이 어떤 질문에 해당하는지 구분해준다.
    """
    if not knowledge_context_groups:
        return "현재 참고할 수 있는 등록 지식이 없습니다."

    lines: list[str] = []

    for group_index, group in enumerate(knowledge_context_groups, start=1):
        query = group.get("query") or f"하위 질문 {group_index}"
        chunks = group.get("chunks", [])

        lines.append(
            f"[질문 {group_index}]\n"
            f"사용자 하위 질문: {query}"
        )

        if not chunks:
            lines.append("참고 지식: 현재 이 질문에 대해 참고할 수 있는 등록 지식이 없습니다.")
            continue

        chunk_lines: list[str] = ["참고 지식:"]

        for chunk_index, item in enumerate(chunks, start=1):
            source_title = item.get("source_title") or "Unknown source"
            content = item.get("content") or ""

            chunk_lines.append(
                f"- 출처: {source_title}\n"
                f"  내용: {content}"
            )

        lines.append("\n".join(chunk_lines))

    return "\n\n".join(lines)


def build_session_context_text(session_state: dict | None) -> str:
    """
    Redis에 저장된 이전 턴의 session_state를 AI instruction용 텍스트로 변환한다.

    이전 응답, 이전 intent, 진행 중인 task 등을 AI가 멀티턴 대화 맥락으로
    참고할 수 있게 한다.
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
    knowledge_context: list[dict],
    knowledge_context_groups: list[dict] | None = None,
    session_state: dict | None = None,
    rules: list[dict] | None = None,
    rule_instructions: str | None = None,
    applied_rules: list[str] | None = None,
) -> str:
    """
    AI 답변 생성을 위한 instructions를 만든다.

    일반 /chat 응답과 WebSocket streaming 응답에서 공통으로 사용한다.

    rules 우선순위:
    1. rule_instructions가 있으면 그대로 사용
    2. rules 목록이 있으면 이름 + 지시문 형태로 변환
    3. applied_rules만 있으면 기존 방식으로 변환
    """

    if rule_instructions:
        rules_text = rule_instructions
    elif rules is not None:
        rules_text = build_rule_instructions_text(rules)
    else:
        rules_text = build_rules_text(applied_rules or [])

    if knowledge_context_groups:
        knowledge_text = build_knowledge_groups_text(knowledge_context_groups)
    else:
        knowledge_text = build_knowledge_text(knowledge_context)

    session_context_text = build_session_context_text(session_state)

    return f"""
너는 Front Agent의 AI 상담사다.

역할:
- 고객의 질문에 친절하고 간결하게 답변한다.
- 등록된 지식과 응답 규칙을 우선으로 따른다.
- 확실하지 않은 내용은 지어내지 않는다.
- 지식에 없는 정보는 담당자 확인이 필요하다고 안내한다.

현재 intent:
{intent}

이전 대화 맥락:
{session_context_text}

반드시 참고해야 할 응답 규칙:
{rules_text}

참고 가능한 지식:
{knowledge_text}

응답 원칙:
- 한국어로 답변한다.
- 고객에게 자연스럽고 친절하게 응답한다.
- 등록된 응답 규칙이 있다면 반드시 그 규칙을 반영한다.
- 너무 길게 설명하지 않는다.
- 가격, 예약, 일정 같은 정보는 등록된 지식에 있을 때만 확정적으로 말한다.
- 예약 생성 태스크가 실제로 성공하기 전에는 예약 완료라고 말하지 않는다.
- 이전 대화 맥락을 참고해 자연스럽게 이어서 답변하되, 이전 맥락과 다른 새 질문이면 새 질문을 우선한다.
- 사용자가 여러 질문을 한 경우, 각 질문에 대해 빠뜨리지 말고 각각 답변한다.
- 일부 질문에 대한 지식만 있으면, 아는 부분은 답변하고 모르는 부분만 담당자 확인이 필요하다고 안내한다.
- 질문에 대한 참고 지식이 없으면, 유사해 보이는 다른 지식으로 추론하거나 대신 답하지 않는다. "해당 정보를 찾지 못했습니다. 담당자에게 문의해 주세요."라고 안내한다.
- 검색된 지식의 출처(서비스명, 상품명 등)가 고객이 물어본 대상과 다르면, 그 지식은 사용하지 않는다. 예: 고객이 "프리미엄 청소"를 물었는데 검색된 지식이 "프리미엄 상담"에 관한 것이면 관련 없는 정보로 간주하고 답하지 않는다.
""".strip()