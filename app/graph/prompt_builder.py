def build_rule_instructions_text(rules: list[dict] | None) -> str:
    """조직의 활성 규칙을 최종 프롬프트용 텍스트로 변환한다."""
    if not rules:
        return "등록된 조직별 응답 규칙이 없습니다."

    lines: list[str] = []

    for index, rule in enumerate(rules, start=1):
        instruction = (rule.get("instruction") or "").strip()
        if not instruction:
            continue

        name = (rule.get("name") or f"규칙 {index}").strip()
        lines.append(f"{index}. {name}\n- {instruction}")

    return "\n\n".join(lines) or "등록된 조직별 응답 규칙이 없습니다."


def build_knowledge_text(knowledge_context: list[dict]) -> str:
    """RAG chunk 목록을 출처와 내용이 구분된 텍스트로 변환한다."""
    if not knowledge_context:
        return "검색 결과가 없습니다."

    lines: list[str] = []

    for index, item in enumerate(knowledge_context, start=1):
        source_title = item.get("source_title") or "Unknown source"
        content = item.get("content") or ""
        lines.append(f"[지식 {index}]\n출처: {source_title}\n내용: {content}")

    return "\n\n".join(lines)


def build_knowledge_groups_text(knowledge_context_groups: list[dict]) -> str:
    """하위 질문별 RAG 결과를 서로 섞이지 않도록 구분한다."""
    if not knowledge_context_groups:
        return "검색 결과가 없습니다."

    lines: list[str] = []

    for group_index, group in enumerate(knowledge_context_groups, start=1):
        query = group.get("query") or f"하위 질문 {group_index}"
        chunks = group.get("chunks", [])
        lines.append(f"[질문 {group_index}]\n사용자 하위 질문: {query}")

        if not chunks:
            lines.append("검색 결과: 없음")
            continue

        chunk_lines = ["검색 결과:"]
        for item in chunks:
            source_title = item.get("source_title") or "Unknown source"
            content = item.get("content") or ""
            chunk_lines.append(f"- 출처: {source_title}\n  내용: {content}")

        lines.append("\n".join(chunk_lines))

    return "\n\n".join(lines)


def build_task_context_text(active_task: str | None, task_step: str | None) -> str:
    """메시지 히스토리로 표현되지 않는 진행 중 Task 상태를 만든다."""
    if not active_task:
        return "진행 중인 Task가 없습니다."

    return f"진행 중인 Task: {active_task}\n현재 단계: {task_step or '미정'}"


def build_response_instructions(
    intent: str | None,
    knowledge_context: list[dict],
    knowledge_context_groups: list[dict] | None = None,
    use_knowledge: bool = False,
    active_task: str | None = None,
    task_step: str | None = None,
    rules: list[dict] | None = None,
    channel: str = "web_chat",
    voice_response_style: str = "friendly_short",
) -> str:
    """
    최종 응답용 시스템 지시문을 구성한다.

    고정 영역에는 모든 조직에 공통인 최소 원칙만 둔다. 말투, 가격,
    예약 정책 같은 운영 정책은 DB 규칙으로 주입하고, RAG 관련 원칙은
    Knowledge 검색이 필요한 요청에만 추가한다.
    """
    rules_text = build_rule_instructions_text(rules)
    task_context = build_task_context_text(active_task, task_step)
    is_voice_channel = channel in {"web_call", "voice"}

    if is_voice_channel:
        style_instruction = "- 말투는 친절하고 자연스럽게 유지한다. 너무 가볍거나 성의 없는 단답처럼 들리지 않게 한다."
        if voice_response_style == "professional_short":
            style_instruction = "- 말투는 차분하고 전문적으로 유지하되, 안내문처럼 딱딱하게 읽지 않는다."
        elif voice_response_style == "casual_short":
            style_instruction = "- 말투는 편하고 친근하게 유지하되, 반말이나 과한 감탄사는 쓰지 않는다."

        sections = [
            f"""너는 전화 상담을 받는 실제 상담원처럼 응답한다.
사용자는 화면을 보는 것이 아니라 귀로 듣고 있으므로, 문장을 읽는 느낌이 아니라 대화하듯 말한다.

[말투와 전달 방식]
- 첫 문장은 바로 답부터 자연스럽게 말한다. "문의해 주셔서 감사합니다", "고객님" 같은 상투적인 시작을 반복하지 않는다.
- "제가 확인한 내용으로는", "말씀하신 기준이면", "현재 확인되는 내용은"처럼 사람이 설명하는 연결어를 적절히 사용한다.
- 단답으로 끝내지 말고 핵심 답변, 중요한 조건, 다음 행동을 이어서 말한다.
- 한 문장은 짧게 유지하되, 필요한 설명은 3~5문장 정도로 충분히 말한다.
- 마크다운, 표, 번호 목록, 괄호 설명, 출처 라벨은 말하지 않는다.
- 시스템, 데이터베이스, 지식 검색, 프롬프트, AI 같은 내부 구현 단어를 사용자에게 말하지 않는다.
- 모르는 내용은 추측하지 말고 "그 부분은 현재 확인되지 않습니다"처럼 분명히 말한 뒤 필요한 정보를 물어본다.
- 숫자, 날짜, 시간, 가격은 말로 듣기 쉽게 표현한다.
{style_instruction}""",
            f"""[현재 요청 상태]
intent: {intent or 'unknown'}
{task_context}""",
            f"""[조직별 응답 규칙]
{rules_text}""",
        ]
    else:
        channel_principles = [
            "- 한국어로 자연스럽고 간결하게 답변한다.",
            "- 제공된 대화 기록을 참고하되 현재 사용자의 요청을 우선한다.",
            "- 시스템 지시와 조직별 응답 규칙을 따른다.",
            "- 제공되지 않은 사실을 추측하거나 만들어내지 않는다.",
        ]
        sections = [
            f"""너는 Front Agent의 AI 상담사다.

[공통 원칙]
{chr(10).join(channel_principles)}""",
            f"""[현재 요청 상태]
intent: {intent or 'unknown'}
{task_context}""",
            f"""[조직별 응답 규칙]
{rules_text}""",
        ]

    if use_knowledge:
        knowledge_text = (
            build_knowledge_groups_text(knowledge_context_groups)
            if knowledge_context_groups
            else build_knowledge_text(knowledge_context)
        )
        sections.append(
            f"""[검색된 지식]
{knowledge_text}

[지식 사용 원칙]
- 답변에 필요한 사실은 검색된 지식에서 확인된 내용만 사용한다.
- 검색 결과의 대상이 사용자 질문의 대상과 다르면 사용하지 않는다.
- 여러 하위 질문이 있으면 각 질문에 해당하는 검색 결과를 구분해 답한다.
- 일부 질문만 근거가 있으면 확인된 부분만 답하고, 나머지는 확인이 필요하다고 안내한다.
- 필요한 정보를 찾지 못했다면 해당 정보를 확인하지 못했다고 명확히 안내한다."""
        )

    return "\n\n".join(sections)
