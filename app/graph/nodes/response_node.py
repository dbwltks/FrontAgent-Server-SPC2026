import logging

from langgraph.config import get_stream_writer
import asyncio
from app.graph.message_utils import history_from_state_messages
from app.graph.prompt_builder import build_response_instructions
from app.graph.state import AgentState
from app.providers.langchain_provider import get_voice_response_style, stream_text


logger = logging.getLogger(__name__)


FALLBACK_RESPONSE = "일시적인 오류로 답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."
DEFAULT_TASK_RESUME_MESSAGE = "예약을 계속하려면 원하시는 서비스를 선택해 주세요."


def stream_direct_message(writer, message: str) -> None:
    """
    LLM을 거치지 않고 서버가 직접 만든 문장도
    프론트에서는 스트리밍처럼 보이도록 단어 단위로 흘려보낸다.
    """
    parts = message.split(" ")

    for index, part in enumerate(parts):
        delta = part if index == 0 else f" {part}"

        writer(
            {
                "type": "ai_response_delta",
                "delta": delta,
            }
        )


def build_service_selection_message_from_task_result(state: dict) -> str | None:
    """
    예약 서비스 선택 단계에서는 LLM이 서비스 목록을 다시 요약하게 하지 않고,
    task_result.variables.available_services.services 기준으로 정확한 선택 문구를 만든다.
    """
    task_result = state.get("task_result") or {}

    if task_result.get("status") != "waiting_user_input":
        return None

    current_node_key = task_result.get("current_node_key")
    if current_node_key != "ask_service":
        return None

    variables = task_result.get("variables") or {}
    available_services = variables.get("available_services") or {}
    services = available_services.get("services") or []

    if not services:
        return None

    service_names = [
        str(service.get("name")).strip()
        for service in services
        if isinstance(service, dict) and service.get("name")
    ]

    if not service_names:
        return None

    service_text = ", ".join(service_names)

    return f"어떤 서비스를 원하시나요? {service_text} 중에서 선택해 주세요."



def build_task_resume_message(pending_task_prompt: str | None) -> str:
    """
    태스크 진행 중 지식 질문에 답한 뒤,
    원래 태스크 단계로 복귀시키기 위한 후속 안내 문구를 만든다.

    이 문구는 final_response에 합치지 않고,
    follow_up_response로 따로 내려보낸다.
    """
    prompt = (pending_task_prompt or "").strip()

    if not prompt:
        return DEFAULT_TASK_RESUME_MESSAGE

    if prompt.startswith("예약을 계속하려면"):
        return prompt

    return f"예약을 계속하려면 {prompt}"


def is_knowledge_question_during_active_task(state: dict) -> bool:
    """
    진행 중인 태스크가 있고,
    사용자의 이번 입력이 태스크 입력값이 아니라 지식 질문으로 분류된 경우인지 확인한다.

    예:
    - 예약 중: "베란다 청소는 어떤거야?"
    - next_action = search_knowledge
    - use_knowledge = True
    """
    return (
        bool(state.get("has_active_task"))
        and bool(state.get("use_knowledge"))
        and state.get("next_action") == "search_knowledge"
    )


def remove_accidental_follow_up_text(
    response: str,
    follow_up_response: str | None,
) -> str:
    """
    LLM이 실수로 예약 복귀 문구를 답변 안에 포함했을 때 제거한다.

    원칙:
    - 지식 답변은 final_response
    - 예약 재질문은 follow_up_response

    둘이 한 문자열에 섞이면 안 된다.
    """
    if not response or not follow_up_response:
        return response

    cleaned = response.strip()

    if follow_up_response in cleaned:
        cleaned = cleaned.replace(follow_up_response, "").strip()

    if DEFAULT_TASK_RESUME_MESSAGE in cleaned:
        cleaned = cleaned.replace(DEFAULT_TASK_RESUME_MESSAGE, "").strip()

    return cleaned

def _get_user_visible_task_message(task_result: dict | None) -> str | None:
    if not isinstance(task_result, dict):
        return None

    variables = task_result.get("variables") or {}

    for value in reversed(list(variables.values())):
        if not isinstance(value, dict):
            continue

        message = value.get("user_visible_message")
        if message:
            return str(message)

    return None

async def response_node(state: AgentState) -> AgentState:
    """
    최종 응답을 생성한다.

    OpenAI 호출 자체는 항상 스트리밍으로 하고, 받은 delta를
    get_stream_writer()로 흘려보낸다.

    중요한 정책:
    1. 일반 답변은 final_response에 저장한다.
    2. 진행 중 태스크의 재질문은 follow_up_response에 저장한다.
    3. 지식 답변과 태스크 재질문을 final_response 하나로 합치지 않는다.
    """
    intent = state.get("intent")
    organization_id = state["organization_id"]

    rules = state.get("rules", [])
    voice_response_style = await get_voice_response_style(organization_id)

    knowledge_context = state.get("knowledge_context", [])
    knowledge_context_groups = state.get("knowledge_context_groups", [])

    user_message = state["user_message"]

    conversation_history = history_from_state_messages(
        state.get("messages", []),
        exclude_current_turn=True,
    )

    is_knowledge_during_active_task = is_knowledge_question_during_active_task(state)

    task_result = state.get("task_result") or {}

    user_visible_task_message = _get_user_visible_task_message(task_result)
    task_message = user_visible_task_message or task_result.get("message")

    task_status = task_result.get("status")

    # Task Runner가 사용자에게 바로 보여줄 메시지를 만든 경우,
    # LLM으로 다시 생성하지 않고 그대로 응답한다.
    # 예: "어떤 서비스를 예약하시겠어요?"
    if (
        task_result.get("handled")
        and task_message
        and task_status in {"waiting_user_input", "completed", "handoff"}
    ):
        writer = get_stream_writer()

        # 기존 LLM 스트리밍처럼 한 글자씩 delta 전송
        for char in task_message:
            writer({"type": "ai_response_delta", "delta": char})
            await asyncio.sleep(0)

        state["final_response"] = task_message
        state["messages"] = [{"role": "assistant", "content": task_message}]
        return state

    instructions = build_response_instructions(
        intent=intent,
        knowledge_context=knowledge_context,
        knowledge_context_groups=knowledge_context_groups,
        use_knowledge=state.get("use_knowledge", False),

        # 중요:
        # 진행 중 태스크에서 지식 질문으로 빠진 경우,
        # LLM에게 active_task 정보를 주지 않는다.
        # 그래야 "예약을 계속하려면 ..." 같은 문구를 LLM이 답변에 섞지 않는다.
        active_task=None if is_knowledge_during_active_task else state.get("active_task"),
        task_step=None if is_knowledge_during_active_task else state.get("task_step"),
        task_result=None if is_knowledge_during_active_task else state.get("task_result"),

        # task_router_node가 판단한 태스크 중간 라우팅 정보
        has_active_task=False
        if is_knowledge_during_active_task
        else state.get("has_active_task", False),
        task_route=None if is_knowledge_during_active_task else state.get("task_route"),
        task_route_reason=None
        if is_knowledge_during_active_task
        else state.get("task_route_reason"),
        pending_task_prompt=None
        if is_knowledge_during_active_task
        else state.get("pending_task_prompt"),
        current_task_node_key=None
        if is_knowledge_during_active_task
        else state.get("current_task_node_key"),

        rules=rules,
        channel=state.get("channel", "web_chat"),
        voice_response_style=voice_response_style,
        should_end_session=bool(state.get("should_end_session")),
    )

    writer = get_stream_writer()
    chunks: list[str] = []

    direct_task_message = build_service_selection_message_from_task_result(state)

    if direct_task_message:
        stream_direct_message(writer, direct_task_message)

        state["final_response"] = direct_task_message
        state["follow_up_response"] = None
        state["messages"] = [
            {
                "role": "assistant",
                "content": direct_task_message,
            }
        ]

        return state

    try:
        async for delta in stream_text(
            organization_id=organization_id,
            instructions=instructions,
            input_text=user_message,
            conversation_history=conversation_history or None,
        ):
            if not delta:
                continue

            chunks.append(delta)
            writer(
                {
                    "type": "ai_response_delta",
                    "delta": delta,
                }
            )

    except Exception:
        logger.warning("response_node LLM call failed", exc_info=True)

        if not chunks:
            chunks = [FALLBACK_RESPONSE]
            writer(
                {
                    "type": "ai_response_delta",
                    "delta": FALLBACK_RESPONSE,
                }
            )

    final_response = "".join(chunks).strip()
    follow_up_response = None

    if is_knowledge_during_active_task:
        follow_up_response = build_task_resume_message(
            state.get("pending_task_prompt")
        )

        final_response = remove_accidental_follow_up_text(
            response=final_response,
            follow_up_response=follow_up_response,
        )

        writer(
            {
                "type": "ai_follow_up_message",
                "message": follow_up_response,
            }
        )

    state["final_response"] = final_response
    state["follow_up_response"] = follow_up_response

    # LLM 대화 히스토리에는 실제 AI 답변만 넣는다.
    # follow_up_response는 시스템성 후속 안내이므로 final_response에 합치지 않는다.
    state["messages"] = [
        {
            "role": "assistant",
            "content": final_response,
        }
    ]

    return state