import asyncio
import json
import logging
import re
from dataclasses import asdict, is_dataclass

from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

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
    has_active_task_session,
    resolve_active_task_step,
    resolve_task_variables,
    slim_task_result,
)
from app.providers.langchain_provider import (
    get_streaming_chat_model,
    get_voice_response_style,
    history_to_messages,
)
from app.rag.keyword_vocabulary import get_organization_keyword_vocabulary
from app.rag.query_matching import looks_like_question, term_appears_in_text
from app.rag.retriever import retrieve_knowledge, summarize_knowledge_chunk
from app.repositories.rule_repo import get_active_rules
from app.repositories.service_repo import list_service_items
from app.tasks.repository import TaskRepository
from app.tasks.runner import DynamicTaskRunner
from app.tasks.service_selection import build_service_selection_message
from app.tasks.trigger_matcher import match_task_trigger


logger = logging.getLogger(__name__)


AGENT_SYSTEM_PROMPT_HEADER = """
너는 실제 상담원처럼 고객 메시지에 응답하는 AI 에이전트다.

도구 사용 원칙:
- 가격·정책·서비스 설명 등 지식 베이스 확인이 필요하면 search_knowledge를 호출한다.
  "입주청소가 뭐에요?", "화장실 청소는 어떤 서비스예요?"처럼 서비스 설명·정의를
  묻는 질문도 search_knowledge다. run_task가 아니다.
  "예약 변경 가능한가요?", "당일 취소되나요?", "주차 가능한가요?"처럼 일반적인
  정책/조건을 묻는 질문형 문장은 정보 문의이므로 search_knowledge를 호출한다.
- 예약 생성·조회·취소·변경을 실제로 지금 실행해 달라는 요청이면 run_task를
  호출한다. "예약하고 싶어요", "OO 예약할게요", "예약해주세요", "변경할게요"처럼
  사용자가 그 행동을 직접 하겠다는 의도가 명확할 때만 호출한다.
  날짜·시간·서비스 종류 같은 세부 정보가 아직 없어도 절대 먼저 직접 물어보지
  말고 무조건 바로 run_task를 호출한다. 그 정보를 모으는 질문은 네가 만드는
  것이 아니라 run_task 호출 결과(이미 다음 질문을 포함하고 있다)를 그대로
  전달하는 것이다.
- "~가능한가요?", "~되나요?", "~할 수 있어요?"처럼 일반 조건을 묻는 의문문은
  run_task가 아니라 search_knowledge로 보낸다. run_task는 사용자가 지금 바로
  그 작업을 시작/이어가려 할 때만 쓴다.
- "어떤 서비스 있어요?", "뭐 파세요?", "메뉴 좀 알려주세요"처럼 예약 가능한
  서비스 종류/목록 자체를 묻는 질문은 지식 베이스 문서가 아니라 예약 시스템의
  서비스 카탈로그에 있는 정보다. search_knowledge로 보내면 지식 문서의 일부
  단편만 가져와 부정확하거나 엉뚱한 항목을 답할 수 있다 - 반드시
  run_task(task_type: reservation_create)로 보낸다. run_task 결과가 정확한
  서비스 목록을 보여준다.
- [현재 요청 상태]에 "진행 중인 Task가 있습니다"라고 나와 있으면, 사용자
  메시지가 예약 단계 답변(성함, 날짜, 시간, 서비스명 등)이면 run_task를
  호출한다.
- 단, 예약 진행 중에도 가격·정책·서비스 설명·조건 확인 등 지식 베이스로
  답해야 하는 질문(예: "취소되나요?", "주차 가능해요?", "뭐 포함돼요?",
  "시간 얼마나 걸려요?")이면 run_task가 아니라 search_knowledge를 호출한다.
  예약을 취소한 것처럼 말하지 말고, 지식 검색으로 답한 뒤 예약 질문을
  이어가면 된다.
- 사람(상담원/직원) 연결을 명시적으로 요청하면 request_handoff를 호출한다.
- 상담·대화·통화를 끝내려는 의도("끊어줘", "통화 종료", "채팅 그만", "여기까지",
  "그만할게요", "이제 됐어요" 등)가 보이면 end_session을 호출한다.
- 위 네 경우가 아닌 인사·잡담·일반 대화는 도구를 호출하지 않고 바로 답한다.
- 한 턴에 도구는 최대 1개만 호출한다. 애매하면 search_knowledge를 우선한다.
- 도구 결과를 받은 뒤에는 그 내용만 근거로 자연스러운 한 번의 답변을 만든다.
- 음성 통화 채널(web_call)에서 search_knowledge나 run_task를 호출하기 전에
  "네.", "알겠습니다.", "확인해드릴게요." 같은 짧고 자연스러운 호응을 먼저 말한 뒤
  도구를 호출한다. 호응은 한 문장, 5단어 이내로 짧게.
""".strip()


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(
        description=(
            "검색에 사용할 한국어 질문. 사용자 원문이 짧거나 모호하면("
            "예: \"가격이 얼마예요?\", \"몇 시까지 해요?\") 대화 맥락(직전에 "
            "언급된 서비스명 등)을 반영해 구체적인 문장으로 보강한다. "
            "예: \"가격이 얼마예요?\" + 직전에 \"베란다 청소\" 언급 → "
            "\"베란다 청소 가격이 얼마예요?\". "
            "이번 메시지에 다른 서비스명이 명시돼 있으면(예: 직전에 입주청소를 "
            "물었더라도 이번에 \"화장실청소 얼마예요?\") 직전 서비스는 넣지 "
            "말고 이번 메시지의 서비스만 검색한다. "
            "맥락이 없으면 일반적인 키워드를 추가한다(예: \"몇 시까지 해요?\" → "
            "\"영업시간이 몇 시까지인가요?\")."
        )
    )


class RunTaskArgs(BaseModel):
    task_type: str = Field(
        description=(
            "reservation_create(새 예약) / reservation_lookup(예약 조회) / "
            "reservation_cancel(예약 취소) / reservation_update(예약 변경) 중 하나."
        )
    )


class RequestHandoffArgs(BaseModel):
    reason: str = Field(description="상담원 연결이 필요한 이유를 한국어로 짧게.")


class EndSessionArgs(BaseModel):
    farewell_message: str = Field(
        description=(
            "사용자에게 들려줄 짧고 따뜻한 작별 인사. "
            "\"통화를 종료하겠습니다\"처럼 시스템적으로 말하지 말고 "
            "\"네, 감사합니다. 좋은 하루 되세요\"처럼 자연스럽게 마무리한다. "
            "추가 질문 여부는 묻지 않는다."
        )
    )


async def _unused_tool_executor(*_args, **_kwargs) -> str:
    """
    네 tool(search_knowledge/run_task/request_handoff/end_session) 모두
    StructuredTool.from_function에 등록만 되고 실제 LangChain 실행 경로로는
    호출되지 않는다 - 모델이 만든 tool_call을 agent_node가 직접 가로채
    tool_name으로 분기 처리하기 때문이다. LangChain StructuredTool.
    from_function이 coroutine 인자를 필수로 요구해서 자리만 채우는
    placeholder다.
    """
    return ""


def _build_service_selection_message(task_result: dict) -> str | None:
    """
    예약 서비스 선택 단계에서는 LLM이 서비스 목록을 다시 요약하게 하지 않고,
    task_result.variables.available_services.services 기준으로 정확한
    선택 문구를 만든다.
    """
    return build_service_selection_message(
        variables=task_result.get("variables") or {},
        current_node_key=task_result.get("current_node_key"),
        status=task_result.get("status"),
    )


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

    # 여러 개가 동시에 잡히면 잘못된 자동 선택을 막기 위해 보류
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

    # 전화번호만 입력한 경우만 처리
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
    """
    최근 대화에서 언급된 예약 서비스 아이템을 찾는다.

    하드코딩하지 않고 service_items DB 목록 기준으로 매칭한다.
    예: "입주 청소가 뭐야" → service_items.name = "입주 청소" 매칭
    """
    try:
        service_items = list_service_items(organization_id=organization_id)
    except Exception:
        logger.exception("Failed to load service items for context bridge")
        return {}

    if not service_items:
        return {}

    # 1순위: 이번 사용자 메시지
    current_match = _match_service_item_in_text(user_message, service_items)
    if current_match:
        return {
            "service_id": current_match.get("service_id"),
            "service_item_id": current_match.get("id"),
            "service_item_name": current_match.get("name"),
            "source": "current_user_message",
        }

    # 2순위: 최근 사용자 메시지
    # assistant가 서비스 목록을 나열한 문장은 여러 서비스가 동시에 잡힐 수 있어서 제외
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
def _extract_service_names_from_pending_prompt(pending: str) -> list[str]:
    match = re.search(r"\?\s*(.+?)\s*중에서\s*선택", pending)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


def _looks_like_task_slot_answer(user_message: str, pending_task_prompt: str | None) -> bool:
    message = user_message.strip()
    if not message:
        return False

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


_TASK_PROGRESS_KEYS = (
    "service_item_id",
    "customer_name",
    "reservation_date",
    "reservation_time",
    "phone",
    "address",
)


def _resolved_service_item_id(variables: dict) -> str | None:
    for key, value in variables.items():
        if not key.endswith("_resolve_result") and key != "resolve_service_item_result":
            continue
        if isinstance(value, dict) and value.get("ok") and value.get("service_item_id"):
            return str(value["service_item_id"])
    direct = variables.get("service_item_id")
    return str(direct) if direct else None


def _task_turn_made_progress(
    *,
    before_step: str | None,
    before_vars: dict,
    after_step: str | None,
    after_vars: dict,
    task_status: str | None,
) -> bool:
    if task_status in ("completed", "handoff"):
        return True
    if before_step and after_step and before_step != after_step:
        return True

    before_service_id = _resolved_service_item_id(before_vars)
    after_service_id = _resolved_service_item_id(after_vars)
    if after_service_id and before_service_id != after_service_id:
        return True

    for key in _TASK_PROGRESS_KEYS:
        if key == "service_item_id":
            continue
        before_val = before_vars.get(key)
        after_val = after_vars.get(key)
        if after_val and str(before_val or "").strip() != str(after_val).strip():
            return True

    return False


def _looks_like_knowledge_interrupt(
    user_message: str,
    pending_task_prompt: str | None = None,
) -> bool:
    """예약 task 진행 중 slot 답변이 아닌 질문(FAQ)인지 판별."""
    message = user_message.strip()
    if not message:
        return False

    if _looks_like_task_slot_answer(message, pending_task_prompt):
        return False

    return looks_like_question(message)


def _compose_direct_knowledge_answer(
    *,
    user_message: str,
    chunks: list[dict],
    clarify_items: list[str],
    resume_task_prompt: str | None,
    skip_service_clarify: bool,
) -> str:
    if clarify_items:
        return (
            f"어떤 서비스를 말씀하시는 걸까요? "
            f"{', '.join(clarify_items)} 중에서 알려주시면 안내해 드릴게요."
        )
    if not chunks:
        return "확인해보니 관련 정보를 찾지 못했습니다. 담당자에게 다시 확인 후 안내드리겠습니다."

    answer_chunks = _prioritize_chunks_for_user_query(user_message, chunks)

    if skip_service_clarify:
        parts: list[str] = []
        seen: set[str] = set()
        for chunk in answer_chunks[:4]:
            summary = summarize_knowledge_chunk(chunk)
            if not summary or summary in seen:
                continue
            seen.add(summary)
            parts.append(summary)
        message = " ".join(parts) if parts else (summarize_knowledge_chunk(answer_chunks[0]) or "")
    else:
        message = summarize_knowledge_chunk(answer_chunks[0]) or ""

    message = message.strip()
    if resume_task_prompt:
        prompt = resume_task_prompt.strip()
        if prompt and prompt not in message:
            message = f"{message}\n\n{prompt}"
    return message


def _resolve_pending_task_prompt(state: AgentState) -> str | None:
    task_result = state.get("task_result") or {}
    message = (task_result.get("message") or "").strip()
    return message or None


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
    """이번 사용자 메시지에 명시된 조직 서비스명(카탈로그 vocabulary 기준)."""
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
    """
    지식 검색 query를 확정한다.

    이번 턴 메시지에 서비스명이 있으면 LLM/직전 턴 맥락을 섞지 않고
    현재 질문만 검색한다. (입주청소 → 화장실청소 연속 질문 꼬임 방지)
    """
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
    """
    서비스 선택 단계에서 '얼마예요?'처럼 특정 서비스 없이 가격만 물을 때는
    clarify(어느 서비스?)로 되돌리지 않고 검색 결과로 전체/복수 안내한다.
    """
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
        "should_use_knowledge": False,
        "should_end_session": False,
        "final_response": message,
        "rules": [],
        "applied_rules": [],
        "messages": [{"role": "assistant", "content": message}],
    }


async def _execute_search_knowledge(
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
    match_count = 5 if _should_skip_service_clarify_for_faq(
        user_message,
        task_step=task_step,
        resume_task_prompt=resume_task_prompt,
    ) else 3
    chunks = await retrieve_knowledge(
        organization_id=organization_id,
        query=search_query,
        match_count=match_count,
    )
    used_knowledge = [
        {
            "chunk_id": c.get("id"),
            "source_id": c.get("source_id"),
            "source_title": c.get("source_title"),
            "similarity": c.get("similarity"),
        }
        for c in chunks
    ]

    skip_service_clarify = _should_skip_service_clarify_for_faq(
        user_message,
        task_step=task_step,
        resume_task_prompt=resume_task_prompt,
    )
    clarify_items: list[str] = []
    if not skip_service_clarify:
        clarify_items = _should_clarify_competing_services(user_message, chunks)

    # 서비스 선택 모호할 때는 코드로 처리 (LLM 불필요)
    if clarify_items:
        direct_message = (
            f"어떤 서비스를 말씀하시는 걸까요? "
            f"{', '.join(clarify_items)} 중에서 알려주시면 안내해 드릴게요."
        )
        writer({"type": "ai_response_delta", "delta": direct_message})
    elif not chunks:
        direct_message = "확인해보니 관련 정보를 찾지 못했습니다. 담당자에게 다시 확인 후 안내드리겠습니다."
        writer({"type": "ai_response_delta", "delta": direct_message})
    else:
        # LLM이 chunk를 보고 자연스럽게 해석해서 답한다.
        # summarize_knowledge_chunk 없이 범용으로 처리.
        answer_chunks = _prioritize_chunks_for_user_query(user_message, chunks)
        context = "\n\n".join(c.get("content", "") for c in answer_chunks[:3])
        system_prompt = (
            "다음 지식을 바탕으로 사용자 질문에 친절하고 자연스럽게 답해라. "
            "지식에 있는 내용만 사용하고 없는 내용은 만들지 않는다. "
            "관련 항목이 여러 개면 모두 포함해라. 간결하게 핵심만 답한다."
        )
        if resume_task_prompt:
            system_prompt += f"\n\n답변 후 예약 진행을 위해 이 질문을 이어가라: {resume_task_prompt}"

        _model = await get_streaming_chat_model(organization_id)
        interpret_msgs = [
            {"role": "system", "content": f"{system_prompt}\n\n[지식]\n{context}"},
            {"role": "user", "content": user_message},
        ]
        chunks_acc: list[str] = []
        async for _chunk in _model.astream(interpret_msgs):
            if _chunk.content:
                chunks_acc.append(_chunk.content)
                writer({"type": "ai_response_delta", "delta": _chunk.content})
        direct_message = "".join(chunks_acc).strip()

    result = {
        "intent": "faq",
        "next_action": "search_knowledge",
        "task_type": "none",
        "use_knowledge": True,
        "should_use_knowledge": True,
        "should_end_session": False,
        "knowledge_context": [],
        "used_knowledge": used_knowledge,
        "final_response": direct_message,
        "messages": [{"role": "assistant", "content": direct_message}],
    }
    if preserve_active_task:
        result["active_task"] = active_task
        result["task_step"] = task_step
        preserved = slim_task_result(task_result) or {}
        if resume_task_prompt and not preserved.get("message"):
            preserved["message"] = resume_task_prompt
        result["task_result"] = preserved or None
    if rules is not None:
        _attach_rules(result, rules)
    return result


def _resolve_task_type_for_continue(state: AgentState) -> str:
    task_type = state.get("task_type")
    if task_type and task_type != "none":
        return task_type
    return "reservation_create"


def _build_end_session_state(*, farewell_message: str, rules: list[dict] | None = None) -> dict:
    message = farewell_message.strip() or "네, 감사합니다. 좋은 하루 되세요."
    payload = {
        "intent": "end_session",
        "next_action": "end_session",
        "task_type": "none",
        "use_knowledge": False,
        "should_use_knowledge": False,
        "should_end_session": True,
        "final_response": message,
        "messages": [{"role": "assistant", "content": message}],
    }
    if rules is not None:
        _attach_rules(payload, rules)
    else:
        payload["rules"] = []
        payload["applied_rules"] = []
    return payload


def _build_handoff_state(*, rules: list[dict] | None = None) -> dict:
    payload = {
        "intent": "handoff",
        "next_action": "handoff",
        "task_type": "none",
        "use_knowledge": False,
        "should_use_knowledge": False,
        "should_end_session": False,
        "final_response": None,
    }
    if rules is not None:
        _attach_rules(payload, rules)
    else:
        payload["rules"] = []
        payload["applied_rules"] = []
    return payload


async def _execute_run_task_turn(
    *,
    organization_id: str,
    session_id: str,
    user_message: str,
    task_type: str,
    writer,
    rules: list[dict] | None = None,
    emit_delta: bool = True,
    flow_id: str | None = None,
    initial_variables: dict | None = None,
) -> dict:
    task_result = await _run_task(
        organization_id,
        session_id,
        user_message,
        task_type,
        flow_id=flow_id,
        initial_variables=initial_variables,
    )

    direct_message = _build_service_selection_message(task_result) or (task_result.get("message") or "").strip()
    if not direct_message:
        direct_message = "요청하신 내용을 처리하지 못했습니다. 다시 한 번 말씀해 주시겠어요?"

    if emit_delta:
        writer({"type": "ai_response_delta", "delta": direct_message})

    task_status = task_result.get("status")
    still_active = task_status == "waiting_user_input"
    slim = slim_task_result(task_result) or {}
    if still_active:
        slim["message"] = direct_message

    payload = {
        "intent": "reservation",
        "next_action": "run_task",
        "task_type": task_type,
        "use_knowledge": False,
        "should_use_knowledge": False,
        "should_end_session": False,
        "active_task": "reservation" if still_active else None,
        "task_step": task_result.get("current_node_key") if still_active else None,
        "task_result": slim if still_active else slim_task_result(task_result),
        "task_status": task_status,
        "final_response": direct_message,
        "messages": [{"role": "assistant", "content": direct_message}],
    }
    if rules is not None:
        _attach_rules(payload, rules)
    else:
        payload["rules"] = []
        payload["applied_rules"] = []
    return payload


async def _run_task(
    organization_id: str,
    session_id: str,
    user_message: str,
    task_type: str,
    *,
    flow_id: str | None = None,
    initial_variables: dict | None = None,
) -> dict:
    repository = TaskRepository()
    runner = DynamicTaskRunner(repository=repository)
    writer = get_stream_writer()

    active_session = repository.find_active_session(organization_id=organization_id, session_id=session_id)

    resolved_flow_id = flow_id
    if active_session is None and resolved_flow_id is None:
        flow = repository.find_enabled_flow_for_task_type(organization_id=organization_id, task_type=task_type)
        if not flow:
            return {"status": "failed", "error": f"task_type에 맞는 활성 태스크가 없습니다: {task_type}"}
        resolved_flow_id = flow["id"]

    task_response = await runner.run(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        flow_id=resolved_flow_id if active_session is None else None,
        initial_variables=initial_variables if active_session is None else None,
        on_trace=lambda item: writer({"type": "task_step", "step": item}),
    )

    if is_dataclass(task_response):
        return asdict(task_response)
    if hasattr(task_response, "model_dump"):
        return task_response.model_dump()
    if isinstance(task_response, dict):
        return task_response
    return {"status": "unknown"}


_VOICE_PREAMBLE_INSTRUCTION = (
    "[최우선 규칙 - 음성 통화]"
    "\nsearch_knowledge나 run_task 도구를 호출할 때는 반드시:"
    "\n1. 먼저 사용자 발화에 맞는 짧은 호응을 텍스트로 출력한다 (예: \"네, 확인해드릴게요.\", \"예약 도와드릴게요.\")"
    "\n2. 그 다음 도구를 호출한다."
    "\n인사·잡담처럼 도구를 쓰지 않는 경우는 호응 없이 바로 답한다."
)


def build_agent_instructions(response_instructions: str, channel: str = "web_chat") -> str:
    return f"{AGENT_SYSTEM_PROMPT_HEADER}\n\n[응답 지시문]\n{response_instructions}"


# coroutine은 모델이 만든 tool_call을 agent_node가 직접 가로채 분기 처리하므로
# 실제로 호출되지 않는 placeholder다(_unused_tool_executor 참고) - 클로저로
# 캡처할 상태가 없으므로 매 턴 재생성할 필요 없이 모듈 레벨로 한 번만 만든다.
AGENT_TOOLS = [
    StructuredTool.from_function(
        coroutine=_unused_tool_executor,
        name="search_knowledge",
        description="가격, 서비스 설명, 정책 등 지식 베이스를 검색한다.",
        args_schema=SearchKnowledgeArgs,
    ),
    StructuredTool.from_function(
        coroutine=_unused_tool_executor,
        name="run_task",
        description="예약 생성/조회/취소/변경을 시작하거나 이어간다.",
        args_schema=RunTaskArgs,
    ),
    StructuredTool.from_function(
        coroutine=_unused_tool_executor,
        name="request_handoff",
        description="사람(상담원/직원) 연결을 요청한다.",
        args_schema=RequestHandoffArgs,
    ),
    StructuredTool.from_function(
        coroutine=_unused_tool_executor,
        name="end_session",
        description="사용자가 상담·대화·통화를 끝내려고 할 때 호출한다.",
        args_schema=EndSessionArgs,
    ),
]
AGENT_TOOLS_BY_NAME = {tool.name: tool for tool in AGENT_TOOLS}


async def agent_node(state: AgentState) -> dict:
    """
    conversation_node + decision_node + rule_node + knowledge_node + task_node +
    response_node가 하던 일을 하나의 Main LLM 호출(+ 필요시 tool 1회 호출)로
    합친다. OpenAI native function calling을 쓴다 - 모델이 직접 search_knowledge/
    run_task/request_handoff/end_session 중 무엇을 부를지 판단하므로, 의도
    분류용 별도 LLM 호출이나 그래프 분기 노드가 필요 없다.

    노드 자체의 호출 순서는:
    1. 활성 규칙 조회(캐시) + (필요 시) 응답 지시문 조립
    2. 진행 중 Task가 있으면 agent LLM을 생략하고 코드로 라우팅한다:
       run_task를 먼저 시도하고, slot이 실제로 채워지지 않았을 때만
       search_knowledge(FAQ)로 넘긴다. 종료/상담원 요청은 각 handler.
    3. Task가 없으면 task_flows 트리거 매칭 → 매칭되면 run_task 직행
    4. 매칭되지 않으면 tool을 묶은 1차 LLM 스트리밍 호출
    5. tool 호출이 없으면 1차 응답이 곧 최종 답변(이미 스트리밍됨)
    6. tool 호출이 있으면 tool을 직접 실행하고, 그 결과를 다시 LLM에 보내
       답변을 재작성시키지 않는다 - tool 결과(검색된 지식, task 노드의 안내
       문구)를 가공 없이 그대로 최종 답변으로 쓴다. 이러면 LLM round-trip이
       항상 1회로 끝나 체감 지연이 절반 가까이 줄어든다(자연스러운 문장
       재구성은 포기한 트레이드오프).
    """
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]
    channel = state.get("channel", "web_chat")
    conversation_history = history_from_state_messages(state.get("messages", []))
    writer = get_stream_writer()

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

    has_active_task = has_active_task_session(
        organization_id,
        session_id,
        active_task=state.get("active_task"),
    )
    pending_task_prompt = _resolve_pending_task_prompt(state)

    rules = await asyncio.to_thread(get_active_rules, organization_id)

    if (
        not has_active_task
        and _looks_like_phone_only_message(user_message)
        and _last_assistant_asked_reservation_lookup_phone(conversation_history)
    ):
        phone = _normalize_phone_text(user_message)

        return await _execute_run_task_turn(
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


    voice_response_style = "friendly_short"
    if channel in {"web_call", "voice"}:
        voice_response_style = await get_voice_response_style(organization_id)

    if has_active_task:
        if is_obvious_end_session_request(user_message):
            farewell_message = "네, 감사합니다. 좋은 하루 되세요."
            writer({"type": "ai_response_delta", "delta": farewell_message})
            return _build_end_session_state(farewell_message=farewell_message, rules=rules)

        if is_obvious_handoff_request(user_message):
            return _build_handoff_state(rules=rules)

        task_type = _resolve_task_type_for_continue(state)
        before_step = resolve_active_task_step(
            organization_id,
            session_id,
            task_step=state.get("task_step"),
        )
        before_vars = resolve_task_variables(organization_id, session_id)

        task_state = await _execute_run_task_turn(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            task_type=task_type,
            writer=writer,
            rules=rules,
            emit_delta=False,
        )

        after_step = task_state.get("task_step") or (task_state.get("task_result") or {}).get("current_node_key")
        after_vars = resolve_task_variables(organization_id, session_id)
        made_progress = _task_turn_made_progress(
            before_step=before_step,
            before_vars=before_vars,
            after_step=after_step,
            after_vars=after_vars,
            task_status=task_state.get("task_status"),
        )

        if made_progress:
            writer({"type": "ai_response_delta", "delta": task_state["final_response"]})
            return task_state

        if _looks_like_knowledge_interrupt(user_message, pending_task_prompt):
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
            return await _execute_search_knowledge(
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

        writer({"type": "ai_response_delta", "delta": task_state["final_response"]})
        return task_state

    if not has_active_task:
        repository = TaskRepository()
        enabled_flows = await asyncio.to_thread(repository.list_enabled_flows, organization_id)
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

            return await _execute_run_task_turn(
                organization_id=organization_id,
                session_id=session_id,
                user_message=user_message,
                task_type=trigger_match.task_type,
                writer=writer,
                rules=rules,
                flow_id=trigger_match.flow_id,
                initial_variables=initial_variables,
            )

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
                "should_use_knowledge": False,
                "final_response": final_response,
                "should_end_session": False,
                "messages": [{"role": "assistant", "content": final_response}],
            },
            rules,
        )

    # tool 호출 처리: 인덱스 0 도구 하나만 지원한다(시스템 프롬프트가 한 턴에
    # 최대 1개만 부르도록 지시한다).
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
                "should_use_knowledge": False,
                "should_end_session": False,
                "final_response": "죄송합니다, 요청을 처리하지 못했습니다.",
            },
            rules,
        )

    if tool_name == "end_session":
        farewell_message = (tool_args.get("farewell_message") or "").strip() or "네, 감사합니다. 좋은 하루 되세요."
        writer({"type": "ai_response_delta", "delta": farewell_message})
        return _build_end_session_state(farewell_message=farewell_message, rules=rules)

    if tool_name == "search_knowledge":
        search_query = _resolve_knowledge_search_query(
            user_message,
            llm_query=tool_args.get("query"),
            organization_id=organization_id,
            session_id=session_id,
        )
        return await _execute_search_knowledge(
            organization_id=organization_id,
            user_message=user_message,
            search_query=search_query,
            writer=writer,
            rules=rules,
            resume_task_prompt=pending_task_prompt if has_active_task else None,
            preserve_active_task=has_active_task,
            active_task=state.get("active_task"),
            task_step=state.get("task_step"),
            task_result=state.get("task_result"),
        )
    elif tool_name == "run_task":
        initial_variables = {}

        if not has_active_task:
            initial_variables = _build_initial_task_variables_from_context(
                organization_id=organization_id,
                user_message=user_message,
                conversation_history=conversation_history,
            )

        return await _execute_run_task_turn(
            organization_id=organization_id,
            session_id=session_id,
            user_message=user_message,
            task_type=tool_args.get("task_type", "reservation_create"),
            writer=writer,
            rules=rules,
            initial_variables=initial_variables,
        )
    # tool_name == "request_handoff"
    return _build_handoff_state(rules=rules)
