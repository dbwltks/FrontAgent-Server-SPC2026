import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass

from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

from app.graph.message_utils import history_from_state_messages
from app.graph.prompt_builder import build_response_instructions
from app.graph.state import AgentState
from app.providers.langchain_provider import (
    get_streaming_chat_model,
    get_voice_response_style,
    history_to_messages,
)
from app.rag.retriever import retrieve_knowledge
from app.repositories.rule_repo import get_active_rules
from app.tasks.repository import TaskRepository
from app.tasks.runner import DynamicTaskRunner


logger = logging.getLogger(__name__)


AGENT_SYSTEM_PROMPT_HEADER = """
너는 실제 상담원처럼 고객 메시지에 응답하는 AI 에이전트다.

도구 사용 원칙:
- 가격·정책·서비스 설명 등 지식 베이스 확인이 필요하면 search_knowledge를 호출한다.
- 예약 생성·조회·취소·변경처럼 실제 상태를 바꾸는 요청이면 run_task를 호출한다.
- 사람(상담원/직원) 연결을 명시적으로 요청하면 request_handoff를 호출한다.
- 상담·대화·통화를 끝내려는 의도("끊어줘", "통화 종료", "채팅 그만", "여기까지",
  "그만할게요", "이제 됐어요" 등)가 보이면 end_session을 호출한다.
- 위 네 경우가 아닌 인사·잡담·일반 대화는 도구를 호출하지 않고 바로 답한다.
- 한 턴에 도구는 최대 1개만 호출한다. 애매하면 search_knowledge를 우선한다.
- 도구 결과를 받은 뒤에는 그 내용만 근거로 자연스러운 한 번의 답변을 만든다.
""".strip()


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(description="검색에 사용할 한국어 질문. 사용자 원문을 최대한 그대로 사용한다.")


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


async def _search_knowledge_tool_fn(organization_id: str, query: str) -> str:
    """
    StructuredTool에 등록만 되고 실제로는 호출되지 않는다(모델이 만든
    tool_call을 agent_node가 직접 가로채 search_knowledge 분기를 처리하기
    때문). LangChain StructuredTool.from_function이 coroutine을 요구해서
    자리만 채우는 placeholder다.
    """
    return ""


def _build_service_selection_message(task_result: dict) -> str | None:
    """
    예약 서비스 선택 단계에서는 LLM이 서비스 목록을 다시 요약하게 하지 않고,
    task_result.variables.available_services.services 기준으로 정확한
    선택 문구를 만든다(app/graph/nodes/response_node.py에 있던 동명 로직을
    tool calling 구조에 맞게 옮겼다).
    """
    if task_result.get("status") != "waiting_user_input":
        return None
    if task_result.get("current_node_key") != "ask_service":
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

    return f"어떤 서비스를 원하시나요? {', '.join(service_names)} 중에서 선택해 주세요."


async def _run_task(organization_id: str, session_id: str, user_message: str, task_type: str) -> dict:
    repository = TaskRepository()
    runner = DynamicTaskRunner(repository=repository)
    writer = get_stream_writer()

    active_session = repository.find_active_session(organization_id=organization_id, session_id=session_id)

    flow_id = None
    if active_session is None:
        flow = repository.find_enabled_flow_for_task_type(organization_id=organization_id, task_type=task_type)
        if not flow:
            return {"status": "failed", "error": f"task_type에 맞는 활성 태스크가 없습니다: {task_type}"}
        flow_id = flow["id"]

    task_response = await runner.run(
        organization_id=organization_id,
        session_id=session_id,
        user_message=user_message,
        flow_id=flow_id,
        on_trace=lambda item: writer({"type": "task_step", "step": item}),
    )

    if is_dataclass(task_response):
        return asdict(task_response)
    if hasattr(task_response, "model_dump"):
        return task_response.model_dump()
    if isinstance(task_response, dict):
        return task_response
    return {"status": "unknown"}


def build_agent_instructions(response_instructions: str) -> str:
    return f"{AGENT_SYSTEM_PROMPT_HEADER}\n\n[응답 지시문]\n{response_instructions}"


async def agent_node(state: AgentState) -> dict:
    """
    conversation_node + decision_node + rule_node + knowledge_node + task_node +
    response_node가 하던 일을 하나의 Main LLM 호출(+ 필요시 tool 1회 호출)로
    합친다. OpenAI native function calling을 쓴다 - 모델이 직접 search_knowledge/
    run_task/request_handoff 중 무엇을 부를지 판단하므로, 의도 분류용 별도
    LLM 호출이나 그래프 분기 노드가 필요 없다.

    노드 자체의 호출 순서는:
    1. 활성 규칙 조회(캐시) + 응답 지시문 조립
    2. tool을 묶은 1차 LLM 스트리밍 호출
    3. tool 호출이 없으면 1차 응답이 곧 최종 답변(이미 스트리밍됨)
    4. tool 호출이 있으면 tool 실행 후 결과를 메시지에 추가해 2차 스트리밍
       호출로 최종 답변을 받는다(OpenAI tool calling 표준 2-step 패턴).
    """
    organization_id = state["organization_id"]
    session_id = state["session_id"]
    user_message = state["user_message"]
    conversation_history = history_from_state_messages(state.get("messages", []))

    rules = await get_active_rules_async(organization_id)
    voice_response_style = await get_voice_response_style(organization_id)
    response_instructions = build_response_instructions(
        intent=None,
        knowledge_context=[],
        use_knowledge=False,
        active_task=state.get("active_task"),
        task_step=state.get("task_step"),
        rules=rules,
        channel=state.get("channel", "web_chat"),
        voice_response_style=voice_response_style,
        should_end_session=False,
    )
    instructions = build_agent_instructions(response_instructions)

    model = await get_streaming_chat_model(organization_id)
    tools = [
        StructuredTool.from_function(
            coroutine=lambda query: _search_knowledge_tool_fn(organization_id, query),
            name="search_knowledge",
            description="가격, 서비스 설명, 정책 등 지식 베이스를 검색한다.",
            args_schema=SearchKnowledgeArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda task_type: _run_task(organization_id, session_id, user_message, task_type),
            name="run_task",
            description="예약 생성/조회/취소/변경을 시작하거나 이어간다.",
            args_schema=RunTaskArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda reason: _search_knowledge_tool_fn(organization_id, reason),
            name="request_handoff",
            description="사람(상담원/직원) 연결을 요청한다.",
            args_schema=RequestHandoffArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda farewell_message: _search_knowledge_tool_fn(organization_id, farewell_message),
            name="end_session",
            description="사용자가 상담·대화·통화를 끝내려고 할 때 호출한다.",
            args_schema=EndSessionArgs,
        ),
    ]
    model_with_tools = model.bind_tools(tools)

    messages = history_to_messages(conversation_history) + [{"role": "user", "content": user_message}]
    system_and_messages = [{"role": "system", "content": instructions}] + messages

    writer = get_stream_writer()
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
        return {
            "final_response": final_response,
            "rules": rules,
            "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
            "should_end_session": False,
            "messages": [{"role": "assistant", "content": final_response}],
        }

    # tool 호출 처리: 인덱스 0 도구 하나만 지원한다(시스템 프롬프트가 한 턴에
    # 최대 1개만 부르도록 지시한다).
    call = tool_call_chunks.get(0) or next(iter(tool_call_chunks.values()))
    tool_name = call["name"]
    try:
        tool_args = json.loads(call["args"]) if call["args"] else {}
    except json.JSONDecodeError:
        tool_args = {}

    tool_by_name = {tool.name: tool for tool in tools}
    tool = tool_by_name.get(tool_name)

    if tool is None or tool_name not in ("search_knowledge", "run_task", "request_handoff", "end_session"):
        return {
            "intent": "general",
            "next_action": "respond_general",
            "task_type": "none",
            "use_knowledge": False,
            "should_use_knowledge": False,
            "should_end_session": False,
            "final_response": "죄송합니다, 요청을 처리하지 못했습니다.",
            "rules": rules,
            "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
        }

    if tool_name == "end_session":
        farewell_message = (tool_args.get("farewell_message") or "").strip() or "네, 감사합니다. 좋은 하루 되세요."
        writer({"type": "ai_response_delta", "delta": farewell_message})
        return {
            "intent": "end_session",
            "next_action": "end_session",
            "task_type": "none",
            "use_knowledge": False,
            "should_use_knowledge": False,
            "should_end_session": True,
            "final_response": farewell_message,
            "rules": rules,
            "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
            "messages": [{"role": "assistant", "content": farewell_message}],
        }

    if tool_name == "search_knowledge":
        writer({"type": "knowledge_start", "queries": [tool_args.get("query", user_message)]})
        intent, next_action = "faq", "search_knowledge"
        chunks = await retrieve_knowledge(
            organization_id=organization_id,
            query=tool_args.get("query", user_message),
            match_count=3,
        )
        knowledge_context = chunks
        used_knowledge = [
            {
                "chunk_id": c.get("id"),
                "source_id": c.get("source_id"),
                "source_title": c.get("source_title"),
                "similarity": c.get("similarity"),
            }
            for c in chunks
        ]

        # tool 결과를 받아 답변을 다시 작성하는 2차 LLM 호출을 생략한다 -
        # 검색된 chunk 내용을 가볍게 이어붙인 텍스트를 그대로 최종 답변으로
        # 쓴다. 자연스러운 문장 재구성은 포기하지만 LLM round-trip을 1번
        # 줄여 체감 지연을 절반 가까이 낮춘다.
        direct_message = (
            "\n".join(c.get("content") or "" for c in chunks).strip()
            if chunks
            else "확인해보니 관련 정보를 찾지 못했습니다. 담당자에게 다시 확인 후 안내드리겠습니다."
        )
        writer({"type": "ai_response_delta", "delta": direct_message})
        return {
            "intent": intent,
            "next_action": next_action,
            "task_type": "none",
            "use_knowledge": True,
            "should_use_knowledge": True,
            "should_end_session": False,
            "knowledge_context": knowledge_context,
            "used_knowledge": used_knowledge,
            "final_response": direct_message,
            "rules": rules,
            "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
            "messages": [{"role": "assistant", "content": direct_message}],
        }
    elif tool_name == "run_task":
        intent, next_action = "reservation", "run_task"
        task_result = await _run_task(organization_id, session_id, user_message, tool_args.get("task_type", "reservation_create"))

        # task 결과를 받아 답변을 다시 작성하는 2차 LLM 호출을 생략한다.
        # 서비스 선택 단계는 정확한 목록이 있으므로 코드로 직접 문장을
        # 조립한다(LLM이 빈 목록을 그럴듯하게 지어내는 사례가 실측됐다 -
        # 빌트인 규칙 "모르면 지어내지 않기"를 어김). 그 외 단계는
        # DynamicTaskRunner가 이미 만들어주는 task_result["message"](각
        # task 노드의 질문/안내 문구)를 그대로 최종 답변으로 쓴다.
        direct_message = _build_service_selection_message(task_result) or (task_result.get("message") or "").strip()
        if not direct_message:
            direct_message = "요청하신 내용을 처리하지 못했습니다. 다시 한 번 말씀해 주시겠어요?"

        writer({"type": "ai_response_delta", "delta": direct_message})
        return {
            "intent": intent,
            "next_action": next_action,
            "task_type": tool_args.get("task_type", "none"),
            "use_knowledge": False,
            "should_use_knowledge": False,
            "should_end_session": False,
            "task_result": task_result,
            "task_status": task_result.get("status"),
            "final_response": direct_message,
            "rules": rules,
            "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
            "messages": [{"role": "assistant", "content": direct_message}],
        }
    # tool_name == "request_handoff"
    return {
        "intent": "handoff",
        "next_action": "handoff",
        "task_type": "none",
        "use_knowledge": False,
        "should_use_knowledge": False,
        "should_end_session": False,
        "final_response": None,
        "rules": rules,
        "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
    }


async def get_active_rules_async(organization_id: str) -> list[dict]:
    return await asyncio.to_thread(get_active_rules, organization_id)
