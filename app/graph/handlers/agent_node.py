import asyncio
import json
import logging
import re

from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer

from app.graph.message_utils import history_from_state_messages
from app.graph.prompt_builder import build_response_instructions
from app.graph.session_end_detection import (
    is_obvious_end_session_request,
    is_obvious_handoff_request,
    try_general_fast_path_response,
)
from app.graph.state import AgentState
from app.graph.task_context import (
    build_task_result_meta,
    load_active_task_session,
    resolve_active_task_step,
    resolve_task_variables,
    slim_task_result,
)
from app.graph.tools import (
    execute_end_session,
    execute_handoff,
    execute_run_task,
    AGENT_TOOL_SCHEMAS,
)
from app.providers.langchain_provider import (
    get_streaming_chat_model,
    get_voice_response_style,
    history_to_messages,
)
from app.rag.keyword_vocabulary import get_organization_keyword_vocabulary
from app.rag.query_matching import looks_like_question, term_appears_in_text
from app.rag.retriever import retrieve_knowledge
from app.repositories.rule_repo import get_active_rules
from app.repositories.service_repo import list_service_items
from app.tasks.repository import TaskRepository
from app.tasks.trigger_matcher import match_task_trigger


logger = logging.getLogger(__name__)


AGENT_SYSTEM_PROMPT_HEADER = """
너는 실제 상담원처럼 고객 메시지에 응답하는 AI 에이전트다.

도구 사용 원칙:
- 예약 생성·조회·취소·변경을 실제로 지금 실행해 달라는 요청이면 run_task를
  호출한다. "예약하고 싶어요", "OO 예약할게요", "예약해주세요", "변경할게요"처럼
  사용자가 그 행동을 직접 하겠다는 의도가 명확할 때만 호출한다.
  날짜·시간·서비스 종류 같은 세부 정보가 아직 없어도 절대 먼저 직접 물어보지
  말고 무조건 바로 run_task를 호출한다.
- "어떤 서비스 있어요?", "뭐 파세요?", "메뉴 좀 알려주세요"처럼 예약 가능한
  서비스 종류/목록 자체를 묻는 질문은 run_task(task_type: reservation_create)로
  보낸다. run_task 결과가 정확한 서비스 목록을 보여준다.
- 사람(상담원/직원) 연결을 명시적으로 요청하면 request_handoff를 호출한다.
- 상담·대화·통화를 끝내려는 의도("끊어줘", "통화 종료", "채팅 그만", "여기까지",
  "그만할게요", "이제 됐어요" 등)가 보이면 end_session을 호출한다.
- 위 세 경우가 아닌 인사·잡담·일반 대화는 도구를 호출하지 않고 바로 답한다.
- 음성 통화 채널(web_call)에서 run_task를 호출하기 전에
  "네.", "알겠습니다.", "확인해드릴게요." 같은 짧고 자연스러운 호응을 먼저 말한다.
  호응은 한 문장, 5단어 이내로 짧게.
""".strip()


from app.graph.tools.schemas import (  # noqa: E402 (schemas import after logger)
    RunTaskArgs,
    RequestHandoffArgs,
    EndSessionArgs,
)


async def _unused_tool_executor(*_args, **_kwargs) -> str:
    # LangChain StructuredTool이 coroutine을 필수로 요구해 자리만 채우는 placeholder.
    # tool_call은 agent_node가 tool_name으로 직접 분기해 execute_* 함수로 실행한다.
    return ""


_SERVICE_ITEM_HEADING = re.compile(
    r"^#{1,6}\s*(?:서비스\s*아이템)\s*:?\s*(\S.*)$",
    re.MULTILINE,
)
KNOWLEDGE_AMBIGUITY_GAP_THRESHOLD = 0.05


def _normalize_service_label(text: str) -> str:
    return re.sub(r"[\s?!.,·]+", "", text.lower())

def _match_service_item_in_text(text: str, service_items: list[dict]) -> dict | None:
    normalized_text = _normalize_service_label(text)
    if not normalized_text:
        return None

    matches: list[dict] = []

    for item in service_items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue

        normalized_name = _normalize_service_label(name)
        if len(normalized_name) < 3:
            continue

        if normalized_name in normalized_text:
            matches.append(item)

    if len(matches) == 1:
        return matches[0]

    return None

def _normalize_phone_text(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 10 and digits.startswith("01") else None


def _looks_like_phone_only_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    phone = _normalize_phone_text(text)
    if not phone:
        return False

    text_without_phone_chars = re.sub(r"[\d\s\-().+]", "", text)
    return not text_without_phone_chars


def _last_assistant_asked_reservation_lookup_phone(
    conversation_history: list[dict],
) -> bool:
    for message in reversed(conversation_history[-6:]):
        if message.get("role") != "assistant":
            continue

        content = str(message.get("content") or "")
        if not content:
            return False

        return (
            "예약" in content
            and ("전화번호" in content or "번호" in content)
            and (
                "조회" in content
                or "찾지 못" in content
                or "확인" in content
                or "다시" in content
            )
        )

    return False


def _find_recent_service_item_context(
    *,
    organization_id: str,
    user_message: str,
    conversation_history: list[dict],
) -> dict:
    try:
        service_items = list_service_items(organization_id=organization_id)
    except Exception:
        logger.exception("Failed to load service items for context bridge")
        return {}

    if not service_items:
        return {}

    current_match = _match_service_item_in_text(user_message, service_items)
    if current_match:
        return {
            "service_id": current_match.get("service_id"),
            "service_item_id": current_match.get("id"),
            "service_item_name": current_match.get("name"),
            "source": "current_user_message",
        }

    for message in reversed(conversation_history[-8:]):
        if message.get("role") != "user":
            continue

        content = str(message.get("content") or "").strip()
        if not content:
            continue

        match = _match_service_item_in_text(content, service_items)
        if match:
            return {
                "service_id": match.get("service_id"),
                "service_item_id": match.get("id"),
                "service_item_name": match.get("name"),
                "source": "recent_user_message",
            }

    return {}


def _build_initial_task_variables_from_context(
    *,
    organization_id: str,
    user_message: str,
    conversation_history: list[dict],
) -> dict:
    service_context = _find_recent_service_item_context(
        organization_id=organization_id,
        user_message=user_message,
        conversation_history=conversation_history,
    )

    if not service_context.get("service_item_id"):
        return {}

    service_item_name = service_context.get("service_item_name")

    return {
        "service_id": service_context.get("service_id"),
        "service_item_id": service_context.get("service_item_id"),
        "service_item_name": service_item_name,
        "service_item_text": service_item_name,
        "context_bridge": {
            "type": "recent_service_item",
            "source": service_context.get("source"),
        },
    }


def _extract_service_item_names(chunks: list[dict], limit: int = 3) -> list[str]:
    names: list[str] = []
    for chunk in chunks[:limit]:
        match = _SERVICE_ITEM_HEADING.search(chunk.get("content") or "")
        if match:
            names.append(match.group(1).strip())
    return names


def _user_message_specifies_service(user_message: str, service_name: str) -> bool:
    msg = _normalize_service_label(user_message)
    service = _normalize_service_label(service_name)
    if len(service) < 2:
        return False
    return service in msg or msg in service


def _find_user_specified_service(user_message: str, chunks: list[dict]) -> str | None:
    seen: set[str] = set()
    for chunk in chunks:
        match = _SERVICE_ITEM_HEADING.search(chunk.get("content") or "")
        if not match:
            continue
        name = match.group(1).strip()
        key = _normalize_service_label(name)
        if key in seen:
            continue
        seen.add(key)
        if _user_message_specifies_service(user_message, name):
            return name
    return None


def _should_clarify_competing_services(
    user_message: str,
    chunks: list[dict],
    *,
    gap_threshold: float = KNOWLEDGE_AMBIGUITY_GAP_THRESHOLD,
) -> list[str]:
    if len(chunks) < 2:
        return []

    gap = (chunks[0].get("similarity") or 0) - (chunks[1].get("similarity") or 0)
    if gap >= gap_threshold:
        return []

    unique_names = list(dict.fromkeys(_extract_service_item_names(chunks)))
    if len(unique_names) < 2:
        return []

    if _find_user_specified_service(user_message, chunks):
        return []

    return unique_names


def _prioritize_chunks_for_user_query(user_message: str, chunks: list[dict]) -> list[dict]:
    specified = _find_user_specified_service(user_message, chunks)
    if not specified:
        return chunks

    specified_norm = _normalize_service_label(specified)
    matching = [
        chunk
        for chunk in chunks
        if specified_norm in _normalize_service_label(chunk.get("content") or "")
    ]
    return matching


_TASK_SLOT_ANSWER_PATTERNS = (
    re.compile(r"^\d{1,2}월"),
    re.compile(r"^\d{4}[-./]\d"),
    re.compile(r"^(내일|모레|오늘|다음\s*주|이번\s*주)"),
    re.compile(r"^\d{1,2}\s*시"),
    re.compile(r"^(오전|오후)\s*\d"),
    re.compile(r"^\d{2,4}-\d{3,4}-\d{4}$"),
    re.compile(r"^(네|아니요|아니|응|좋아요|없어요|있어요|없습니다|있습니다)$"),
)
_SERVICE_LIST_PATTERNS = re.compile(
    r"(어떤|무슨|어느|뭐|뭘|몇가지|몇\s*개).{0,12}(서비스|청소|메뉴|상품|종류|항목).{0,4}(있|해|드려|파|하|요|나요|어요)"
    r"|(서비스|청소).{0,5}(목록|종류|리스트|뭐가|어떤게|어떤\s*게|있어)"
    r"|(청소|서비스).{0,8}(뭐뭐|어떤것|어떤\s*것|다\s*알려|종류가)",
    re.IGNORECASE,
)


def _looks_like_service_list_question(message: str) -> bool:
    """서비스/청소 종류 목록 자체를 묻는 질문 — RAG가 아닌 run_task로 처리해야 함."""
    return bool(_SERVICE_LIST_PATTERNS.search(message.strip()))


_TASK_ACTION_INTENT_PATTERN = re.compile(
    r"(예약|취소|조회).{0,10}(할\s*수\s*있|가능한가|가능해|되나요|되나|돼요)"
)


def _looks_like_task_action_question(message: str) -> bool:
    """"예약할 수 있나요?"처럼 의문형이지만 실제로는 예약/취소/조회를 하고 싶다는
    의도인 문장 — trigger_matcher가 문자열 매칭에 실패해도, RAG로 보내지 않고
    agent LLM(3d, run_task 포함 tool-calling) 판단까지 가게 한다."""
    return bool(_TASK_ACTION_INTENT_PATTERN.search(message.strip()))


_TASK_TYPE_INTENT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "reservation_cancel",
        re.compile(r"취소.{0,10}(해줘|해주|할래|하고\s*싶|부탁|할\s*수\s*있|가능)"),
    ),
    (
        "reservation_lookup",
        re.compile(r"조회.{0,10}(해줘|해주|할래|하고\s*싶|부탁|할\s*수\s*있|가능)"),
    ),
    (
        "reservation_create",
        re.compile(r"예약.{0,10}(해줘|해주|할래|하고\s*싶|잡아|부탁|할\s*수\s*있|가능)"),
    ),
)


def _detect_new_task_intent(message: str) -> str | None:
    """활성 태스크 도중 사용자가 명확히 다른 태스크(예약/취소/조회)를 원한다는
    신호가 있으면 그 task_type을 반환한다. 순서상 취소·조회를 먼저 검사해야
    "예약 취소해줘"가 reservation_create로 오판되지 않는다."""
    msg = message.strip()
    for task_type, pattern in _TASK_TYPE_INTENT_PATTERNS:
        if pattern.search(msg):
            return task_type
    return None


def _extract_service_names_from_pending_prompt(pending: str) -> list[str]:
    match = re.search(r"\?\s*(.+?)\s*중에서\s*선택", pending)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


_AFFIRM_OR_DENY_PREFIX = re.compile(r"^(네|넵|예|응|어|아니요|아니오|아니)\b")


def _looks_like_task_slot_answer(user_message: str, pending_task_prompt: str | None) -> bool:
    message = user_message.strip()
    if not message:
        return False

    # 태스크가 "~해드릴까요?" 같은 예/아니오 확인 질문을 던진 상태라면, 사용자
    # 답이 "네 자세히 알려주세요"처럼 긍정/부정 뒤에 부가 설명이 붙어도 그
    # 확인 질문에 대한 응답이다. 뒤에 "알려주세요"가 붙었다는 이유만으로
    # looks_like_question이 True가 되어 지식 질문으로 오분류되는 것을 막는다.
    if (pending_task_prompt or "").rstrip().endswith("?") and _AFFIRM_OR_DENY_PREFIX.match(message):
        return True

    if looks_like_question(message):
        return False

    for pattern in _TASK_SLOT_ANSWER_PATTERNS:
        if pattern.search(message):
            return True

    pending = pending_task_prompt or ""
    if pending:
        norm_msg = _normalize_service_label(message)
        for service_name in _extract_service_names_from_pending_prompt(pending):
            norm_name = _normalize_service_label(service_name)
            if norm_name and norm_name in norm_msg:
                return True

    if ("성함" in pending or "이름" in pending) and re.fullmatch(r"[가-힣]{2,6}", message):
        return True
    if ("날짜" in pending or "시간" in pending) and re.search(r"\d|월|일|시|내일|모레", message):
        return True
    if "주소" in pending and re.search(r"(로|길|동|구|시|\d)", message) and "?" not in message:
        return True
    if "평수" in pending and re.fullmatch(r"\d+\s*평?", message):
        return True

    return False


def _looks_like_knowledge_question(
    user_message: str,
    pending_task_prompt: str | None = None,
) -> bool:
    """슬롯 답변이 아닌 지식/정책 질문인지 판별."""
    message = user_message.strip()
    if not message:
        return False

    if _looks_like_task_slot_answer(message, pending_task_prompt):
        return False

    return looks_like_question(message)


def _extract_available_service_names(variables: dict) -> list[str]:
    available = variables.get("available_services") or {}
    services = available.get("services") or []
    names: list[str] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        name = str(service.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _find_services_mentioned_in_message(user_message: str, organization_id: str) -> list[str]:
    vocabulary = get_organization_keyword_vocabulary(organization_id)

    candidates: list[str] = []
    seen_norms: set[str] = set()
    for term in sorted(vocabulary.terms, key=len, reverse=True):
        if len(_normalize_service_label(term)) < 4:
            continue
        if not term_appears_in_text(term, user_message):
            continue
        norm_term = _normalize_service_label(term)
        if norm_term in seen_norms:
            continue
        seen_norms.add(norm_term)
        candidates.append(term)

    if len(candidates) <= 1:
        return candidates

    filtered: list[str] = []
    norms = [_normalize_service_label(term) for term in candidates]
    for index, term in enumerate(candidates):
        norm = norms[index]
        if any(norm != other and norm in other for other in norms):
            continue
        filtered.append(term)
    return filtered


def _resolve_knowledge_search_query(
    user_message: str,
    llm_query: str | None,
    organization_id: str,
    session_id: str,
) -> str:
    message = user_message.strip()
    if not message:
        return (llm_query or "").strip()

    if _find_services_mentioned_in_message(message, organization_id):
        query = message
        if _is_generic_price_question(message) and "가격" not in query:
            query = f"{query} 가격"
        return query

    enriched = (llm_query or "").strip()
    if enriched and enriched != message:
        return enriched

    return _build_knowledge_search_query(message, organization_id, session_id)


def _build_knowledge_search_query(
    user_message: str,
    organization_id: str,
    session_id: str,
) -> str:
    query_parts = [user_message.strip()]
    variables = resolve_task_variables(organization_id, session_id)
    for key in (
        "service_item_name",
        "service_item_text",
        "resolved_service_item_name",
        "service_name",
    ):
        value = variables.get(key)
        if not value:
            continue
        text = str(value).strip()
        if text and text.lower() not in user_message.lower():
            query_parts.append(text)

    service_names = _extract_available_service_names(variables)
    normalized_message = _normalize_service_label(user_message)
    has_service_in_message = any(
        _normalize_service_label(name) in normalized_message for name in service_names
    )
    if service_names and not has_service_in_message:
        query_parts.extend(service_names[:6])

    deduped_parts: list[str] = []
    seen_labels: set[str] = set()
    for part in query_parts:
        text = part.strip()
        if not text:
            continue
        label = _normalize_service_label(text)
        if label in seen_labels:
            continue
        seen_labels.add(label)
        deduped_parts.append(text)

    query = " ".join(deduped_parts)
    if re.search(r"얼마|가격|비용|요금", user_message) and "가격" not in query:
        query = f"{query} 가격"
    return query


def _is_generic_price_question(user_message: str) -> bool:
    return bool(re.search(r"얼마|가격|비용|요금", user_message))


def _should_skip_service_clarify_for_faq(
    user_message: str,
    *,
    task_step: str | None,
    resume_task_prompt: str | None,
) -> bool:
    if task_step != "ask_service":
        return False
    if not _is_generic_price_question(user_message):
        return False
    pending = resume_task_prompt or ""
    return "어떤 서비스" in pending or "선택" in pending


def _applied_rule_names(rules: list[dict]) -> list[str]:
    return [rule.get("name", "unnamed_rule") for rule in rules]


def _attach_rules(payload: dict, rules: list[dict]) -> dict:
    payload["rules"] = rules
    payload["applied_rules"] = _applied_rule_names(rules)
    return payload


def _build_general_response_state(*, message: str) -> dict:
    return {
        "intent": "general",
        "next_action": "respond_general",
        "task_type": "none",
        "use_knowledge": False,
        "should_end_session": False,
        "final_response": message,
        "rules": [],
        "applied_rules": [],
        "messages": [{"role": "assistant", "content": message}],
    }


async def _call_search_knowledge(
    *,
    organization_id: str,
    user_message: str,
    search_query: str,
    writer,
    resume_task_prompt: str | None = None,
    preserve_active_task: bool = False,
    active_task: str | None = None,
    task_step: str | None = None,
    task_result: dict | None = None,
    rules: list[dict] | None = None,
) -> dict:
    writer({"type": "knowledge_start", "queries": [search_query]})

    skip_clarify = _should_skip_service_clarify_for_faq(
        user_message, task_step=task_step, resume_task_prompt=resume_task_prompt,
    )
    chunks = await retrieve_knowledge(
        organization_id=organization_id,
        query=search_query,
        match_count=5 if skip_clarify else 3,
    )

    # 모호한 서비스 질문 → LLM 없이 코드로 응답
    if not skip_clarify:
        clarify_items = _should_clarify_competing_services(user_message, chunks)
        if clarify_items:
            msg = f"어떤 서비스를 말씀하시는 걸까요? {', '.join(clarify_items)} 중에서 알려주시면 안내해 드릴게요."
            writer({"type": "ai_response_delta", "delta": msg})
            return _attach_rules(
                {
                    "intent": "faq", "next_action": "search_knowledge", "task_type": "none",
                    "use_knowledge": True, "should_end_session": False,
                    "knowledge_context": [], "used_knowledge": [],
                    "final_response": msg,
                    "messages": [{"role": "assistant", "content": msg}],
                },
                rules or [],
            )

    # 검색 결과 없음
    if not chunks:
        msg = "확인해보니 관련 정보를 찾지 못했습니다. 담당자에게 다시 확인 후 안내드리겠습니다."
        writer({"type": "ai_response_delta", "delta": msg})
        return _attach_rules(
            {
                "intent": "faq", "next_action": "search_knowledge", "task_type": "none",
                "use_knowledge": False, "should_end_session": False,
                "knowledge_context": [], "used_knowledge": [],
                "final_response": msg,
                "messages": [{"role": "assistant", "content": msg}],
            },
            rules or [],
        )

    # RAG → LLM 단일 호출로 답변 스트리밍
    used_knowledge = [
        {"chunk_id": c.get("id"), "source_id": c.get("source_id"),
         "source_title": c.get("source_title"), "similarity": c.get("similarity")}
        for c in chunks
    ]
    context = "\n\n".join(c.get("content", "") for c in chunks[:3])
    system_prompt = (
        "다음 지식을 바탕으로 사용자 질문에 친절하고 자연스럽게 답해라. "
        "지식에 있는 내용만 사용하고 없는 내용은 만들지 않는다. "
        "관련 항목이 여러 개면 모두 포함해라. 간결하게 핵심만 답한다."
    )
    if resume_task_prompt:
        system_prompt += f"\n\n답변 후 예약 진행을 위해 이 질문을 이어가라: {resume_task_prompt}"

    model = await get_streaming_chat_model(organization_id)
    tokens: list[str] = []
    async for chunk in model.astream([
        {"role": "system", "content": f"{system_prompt}\n\n[지식]\n{context}"},
        {"role": "user", "content": user_message},
    ]):
        if chunk.content:
            tokens.append(chunk.content)
            writer({"type": "ai_response_delta", "delta": chunk.content})
    final_response = "".join(tokens).strip()

    result: dict = {
        "intent": "faq", "next_action": "search_knowledge", "task_type": "none",
        "use_knowledge": True, "should_end_session": False,
        "knowledge_context": [], "used_knowledge": used_knowledge,
        "final_response": final_response,
        "messages": [{"role": "assistant", "content": final_response}],
    }
    if preserve_active_task:
        result["active_task"] = active_task
        result["task_step"] = task_step
        preserved = slim_task_result(task_result) or {}
        if resume_task_prompt and not preserved.get("message"):
            preserved["message"] = resume_task_prompt
        result["task_result"] = preserved or None

    return _attach_rules(result, rules or [])


def _resolve_task_type_for_continue(state: AgentState) -> str:
    task_type = state.get("task_type")
    if task_type and task_type != "none":
        return task_type
    return "reservation_create"


async def _call_run_task(
    *,
    organization_id: str,
    session_id: str,
    user_message: str,
    task_type: str,
    writer,
    rules: list[dict] | None = None,
    flow_id: str | None = None,
    initial_variables: dict | None = None,
) -> dict:
    result = await execute_run_task(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        task_type=task_type,
        on_delta=lambda d: writer({"type": "ai_response_delta", "delta": d}),
        on_trace=lambda item: writer({"type": "task_step", "step": item}),
        flow_id=flow_id,
        initial_variables=initial_variables,
    )
    if rules is not None:
        _attach_rules(result, rules)
    else:
        result.setdefault("rules", [])
        result.setdefault("applied_rules", [])
    return result


def build_agent_instructions(response_instructions: str, channel: str = "web_chat") -> str:
    return f"{AGENT_SYSTEM_PROMPT_HEADER}\n\n[응답 지시문]\n{response_instructions}"


AGENT_TOOLS = [
    StructuredTool.from_function(
        coroutine=_unused_tool_executor,
        name=s["name"],
        description=s["description"],
        args_schema=s["args_schema"],
    )
    for s in AGENT_TOOL_SCHEMAS
]
AGENT_TOOLS_BY_NAME = {tool.name: tool for tool in AGENT_TOOLS}


async def agent_node(state: AgentState) -> dict:
    """
    라우팅은 코드, 생성은 LLM 1번.

    실행 순서:
    1. 인사/잡담 fast path (코드, LLM 없음)
    2. 활성 태스크 있음
       a. 종료/핸드오프 패턴 → 코드 응답
       b. 지식 질문 패턴 → RAG + 단일 LLM (tool calling 없음)
       c. 슬롯 답변 → run_task 직행 (instruction LLM 1번)
    3. 활성 태스크 없음
       a. 전화번호만 입력 → reservation_lookup 직행
       b. trigger 매칭 → run_task 직행 (instruction LLM 1번)
       c. 나머지 → agent LLM tool calling (1번)
          - search_knowledge → RAG + 단일 LLM (총 1번)
          - run_task → instruction LLM (1번)
          - end_session / request_handoff → 코드 응답
    """
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]
    channel = state.get("channel", "web_chat")
    conversation_history = history_from_state_messages(state.get("messages", []))
    writer = get_stream_writer()

    # 1. 인사/잡담 fast path
    if not state.get("active_task"):
        has_prior_assistant_turn = any(
            msg.get("role") == "assistant" for msg in conversation_history
        )
        fast_response = try_general_fast_path_response(
            user_message,
            has_prior_assistant_turn=has_prior_assistant_turn,
        )
        if fast_response:
            writer({"type": "ai_response_delta", "delta": fast_response})
            return _build_general_response_state(message=fast_response)

    pending_task_prompt = (state.get("task_result") or {}).get("message") or None
    is_voice = channel in {"web_call", "voice"}
    repository = TaskRepository()

    # state에 active_task가 이미 있으면 Redis 조회 없이 즉시 확정
    _active_task_from_state = state.get("active_task")
    has_active_task: bool

    if _active_task_from_state:
        has_active_task = True
        # active_task 확정 시 enabled_flows 조회 불필요 (trigger 매칭 안 함)
        async def _noop_style() -> str:
            return "friendly_short"

        rules, voice_response_style = await asyncio.gather(
            asyncio.to_thread(get_active_rules, organization_id),
            get_voice_response_style(organization_id) if is_voice else _noop_style(),
        )
        enabled_flows = []
    else:
        # rules + Redis 세션 확인 + enabled_flows + voice style 병렬 조회
        async def _fetch_voice_style() -> str:
            if not is_voice:
                return "friendly_short"
            return await get_voice_response_style(organization_id)

        (
            rules,
            has_active_task,
            enabled_flows,
            voice_response_style,
        ) = await asyncio.gather(
            asyncio.to_thread(get_active_rules, organization_id),
            asyncio.to_thread(load_active_task_session, organization_id, session_id),
            asyncio.to_thread(repository.list_enabled_flows, organization_id),
            _fetch_voice_style(),
        )
        # load_active_task_session 결과를 bool로 변환
        has_active_task = has_active_task is not None

    # 2. 활성 태스크 있음 → 코드로 분기, LLM tool calling 없음
    if has_active_task:
        # 2a. 종료/핸드오프
        if is_obvious_end_session_request(user_message):
            farewell_message = "네, 감사합니다. 좋은 하루 되세요."
            writer({"type": "ai_response_delta", "delta": farewell_message})
            return _attach_rules(execute_end_session(farewell_message=farewell_message), rules)

        if is_obvious_handoff_request(user_message):
            return _attach_rules(execute_handoff(), rules)

        # 2b. 지식 질문 패턴 → RAG + 단일 LLM (run_task 헛돌이 없음)
        if _looks_like_knowledge_question(user_message, pending_task_prompt):
            task_result_meta = build_task_result_meta(
                task_result=state.get("task_result"),
                organization_id=organization_id,
                session_id=session_id,
            )
            resume_prompt = pending_task_prompt or (task_result_meta or {}).get("message")
            search_query = _resolve_knowledge_search_query(
                user_message,
                llm_query=None,
                organization_id=organization_id,
                session_id=session_id,
            )
            return await _call_search_knowledge(
                organization_id=organization_id,
                user_message=user_message,
                search_query=search_query,
                writer=writer,
                rules=rules,
                resume_task_prompt=resume_prompt,
                preserve_active_task=True,
                active_task=state.get("active_task") or "reservation",
                task_step=resolve_active_task_step(
                    organization_id,
                    session_id,
                    task_step=state.get("task_step"),
                ),
                task_result=task_result_meta,
            )

        # 2b-2. 진행 중인 태스크와 무관한 새 태스크 의도 → 기존 태스크를 정리하고 새로 시작
        # (예: 예약 조회 중인데 "예약할래요"처럼 완전히 다른 요청이 온 경우).
        # 그대로 run_task에 넘기면 execute_run_task가 활성 세션(조회)을 그대로
        # 이어가 사용자 메시지를 조회 플로우의 슬롯 답변으로 오인하게 된다.
        new_task_intent = _detect_new_task_intent(user_message)
        if new_task_intent:
            task_result_meta = build_task_result_meta(
                task_result=state.get("task_result"),
                organization_id=organization_id,
                session_id=session_id,
            )
            current_flow_id = (task_result_meta or {}).get("flow_id")
            current_flow = repository.get_flow(current_flow_id) if current_flow_id else None
            current_task_type = str((current_flow or {}).get("trigger_intent") or "")

            if current_task_type and current_task_type != new_task_intent:
                current_task_session_id = (task_result_meta or {}).get("task_session_id")
                if current_task_session_id:
                    repository.update_session(
                        current_task_session_id,
                        {"status": "cancelled"},
                        organization_id=organization_id,
                        session_id=session_id,
                    )
                new_flow = repository.find_enabled_flow_for_task_type(
                    organization_id=organization_id,
                    task_type=new_task_intent,
                )
                initial_variables = _build_initial_task_variables_from_context(
                    organization_id=organization_id,
                    user_message=user_message,
                    conversation_history=conversation_history,
                )
                return await _call_run_task(
                    organization_id=organization_id,
                    session_id=session_id,
                    user_message=user_message,
                    task_type=new_task_intent,
                    writer=writer,
                    rules=rules,
                    flow_id=new_flow["id"] if new_flow else None,
                    initial_variables=initial_variables,
                )

        # 2c. 슬롯 답변 → run_task 직행
        task_type = _resolve_task_type_for_continue(state)
        return await _call_run_task(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            task_type=task_type,
            writer=writer,
            rules=rules,
        )

    # 3. 활성 태스크 없음

    # 3a. 전화번호만 → reservation_lookup 직행
    if (
        _looks_like_phone_only_message(user_message)
        and _last_assistant_asked_reservation_lookup_phone(conversation_history)
    ):
        phone = _normalize_phone_text(user_message)
        return await _call_run_task(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            task_type="reservation_lookup",
            writer=writer,
            rules=rules,
            initial_variables={
                "customer_phone": phone,
                "phone": phone,
            },
        )

    # 3b. trigger 매칭 → run_task 직행
    trigger_match = match_task_trigger(
        user_message,
        enabled_flows,
        channel=channel,
    )
    if trigger_match:
        writer(
            {
                "type": "task_trigger_matched",
                "flow_id": trigger_match.flow_id,
                "task_type": trigger_match.task_type,
                "reason": trigger_match.match_reason,
                "score": trigger_match.score,
            }
        )
        initial_variables = _build_initial_task_variables_from_context(
            organization_id=organization_id,
            user_message=user_message,
            conversation_history=conversation_history,
        )
        return await _call_run_task(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            task_type=trigger_match.task_type,
            writer=writer,
            rules=rules,
            flow_id=trigger_match.flow_id,
            initial_variables=initial_variables,
        )

    # 3c. 서비스 목록 질문, 예약/취소/조회 의도 질문은 agent LLM으로
    # (run_task가 정확히 처리해야 하는데, RAG로 보내면 지식문서에 없으니 헛돈다)
    if _looks_like_service_list_question(user_message) or _looks_like_task_action_question(user_message):
        pass  # fall through to 3d
    # 3c-2. 지식 질문 → 코드 라우팅으로 직행 (LLM 1번만 사용)
    elif looks_like_question(user_message):
        search_query = _resolve_knowledge_search_query(
            user_message,
            llm_query=None,
            organization_id=organization_id,
            session_id=session_id,
        )
        return await _call_search_knowledge(
            organization_id=organization_id,
            user_message=user_message,
            search_query=search_query,
            writer=writer,
            rules=rules,
        )

    # 3d. 나머지(인사/잡담/예약요청/종료) → agent LLM (run_task·handoff·end_session만)
    response_instructions = build_response_instructions(
        intent=None,
        knowledge_context=[],
        use_knowledge=False,
        active_task=state.get("active_task"),
        task_step=state.get("task_step"),
        task_result=state.get("task_result"),
        has_active_task=has_active_task,
        pending_task_prompt=pending_task_prompt,
        rules=rules,
        channel=channel,
        voice_response_style=voice_response_style,
        should_end_session=False,
    )
    instructions = build_agent_instructions(response_instructions, channel=channel)

    model = await get_streaming_chat_model(organization_id)
    model_with_tools = model.bind_tools(AGENT_TOOLS)

    messages = history_to_messages(conversation_history) + [{"role": "user", "content": user_message}]
    system_and_messages = [{"role": "system", "content": instructions}] + messages

    tool_call_chunks: dict[int, dict] = {}
    text_chunks: list[str] = []
    has_tool_call = False

    async for chunk in model_with_tools.astream(system_and_messages):
        if chunk.tool_call_chunks:
            has_tool_call = True
            for tc in chunk.tool_call_chunks:
                index = tc.get("index") or 0
                entry = tool_call_chunks.setdefault(index, {"name": "", "args": "", "id": None})
                if tc.get("name"):
                    entry["name"] += tc["name"]
                if tc.get("args"):
                    entry["args"] += tc["args"]
                if tc.get("id"):
                    entry["id"] = tc["id"]
            continue

        if chunk.content and not has_tool_call:
            text_chunks.append(chunk.content)
            writer({"type": "ai_response_delta", "delta": chunk.content})

    if not has_tool_call:
        final_response = "".join(text_chunks).strip()
        return _attach_rules(
            {
                "intent": "general",
                "next_action": "respond_general",
                "task_type": "none",
                "use_knowledge": False,
                "final_response": final_response,
                "should_end_session": False,
                "messages": [{"role": "assistant", "content": final_response}],
            },
            rules,
        )

    call = tool_call_chunks.get(0) or next(iter(tool_call_chunks.values()))
    tool_name = call["name"]
    try:
        tool_args = json.loads(call["args"]) if call["args"] else {}
    except json.JSONDecodeError:
        tool_args = {}

    if tool_name not in AGENT_TOOLS_BY_NAME:
        return _attach_rules(
            {
                "intent": "general",
                "next_action": "respond_general",
                "task_type": "none",
                "use_knowledge": False,
                "should_end_session": False,
                "final_response": "죄송합니다, 요청을 처리하지 못했습니다.",
            },
            rules,
        )

    if tool_name == "end_session":
        farewell_message = (tool_args.get("farewell_message") or "").strip() or "네, 감사합니다. 좋은 하루 되세요."
        writer({"type": "ai_response_delta", "delta": farewell_message})
        return _attach_rules(execute_end_session(farewell_message=farewell_message), rules)

    if tool_name == "request_handoff":
        return _attach_rules(execute_handoff(), rules)

    # tool_name == "run_task"
    initial_variables = _build_initial_task_variables_from_context(
        organization_id=organization_id,
        user_message=user_message,
        conversation_history=conversation_history,
    )
    return await _call_run_task(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        task_type=tool_args.get("task_type", "reservation_create"),
        writer=writer,
        rules=rules,
        initial_variables=initial_variables,
    )
