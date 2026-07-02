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
from app.graph.state import AgentState
from app.providers.langchain_provider import (
    get_streaming_chat_model,
    get_voice_response_style,
    history_to_messages,
)
from app.rag.retriever import retrieve_knowledge, summarize_knowledge_chunk
from app.repositories.rule_repo import get_active_rules
from app.tasks.repository import TaskRepository
from app.tasks.runner import DynamicTaskRunner


logger = logging.getLogger(__name__)

# tool 호출 전 "네/알겠습니다/확인해드릴게요" 같은 짧은 호응(preamble) 기능.
# False면 프롬프트·스트리밍 모두에서 호응을 쓰지 않는다.
AGENT_PREAMBLE_ENABLED = False

NO_AGENT_PREAMBLE_INSTRUCTION = """
[호응(preamble) 비활성 — 최우선]
- "네", "알겠습니다", "확인해드릴게요", "잠시만요", "예약 도와드릴게요"처럼
  본답 없이 짧게 받아주는 호응만 단독으로 출력하지 않는다.
- search_knowledge/run_task 등 도구를 호출할 때 tool 호출 전·후에
  별도 호응 문장을 붙이지 않는다. tool 실행 결과(또는 tool 없이 만든 본답)만 전달한다.
- 도구를 쓰는 턴에는 확인·요약·전환 멘트("~로 진행할게요" 등)를 앞에 붙이지 말고
  task/지식 검색이 준 문장만 그대로 출력한다.
""".strip()


AGENT_SYSTEM_PROMPT_HEADER = """
너는 실제 상담원처럼 고객 메시지에 응답하는 AI 에이전트다.

도구 사용 원칙:
- 가격·정책·서비스 설명 등 지식 베이스 확인이 필요하면 search_knowledge를 호출한다.
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
- [현재 요청 상태]에 "진행 중인 Task가 있습니다"라고 나와 있으면, 이번 사용자
  메시지는 그 예약을 이어가기 위한 답변(날짜, 시간, 서비스명, 인원수 등)일
  가능성이 매우 높다. 이 경우에도 task_type을 reservation_create로 그대로
  run_task를 다시 호출한다 - 직접 답하지 말고 항상 run_task에 맡긴다.
- 사람(상담원/직원) 연결을 명시적으로 요청하면 request_handoff를 호출한다.
- 상담·대화·통화를 끝내려는 의도("끊어줘", "통화 종료", "채팅 그만", "여기까지",
  "그만할게요", "이제 됐어요" 등)가 보이면 end_session을 호출한다.
- 위 네 경우가 아닌 인사·잡담·일반 대화는 도구를 호출하지 않고 바로 답한다.
- 한 턴에 도구는 최대 1개만 호출한다. 애매하면 search_knowledge를 우선한다.
""".strip()


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(
        description=(
            "검색에 사용할 한국어 질문. 사용자 원문이 짧거나 모호하면("
            "예: \"가격이 얼마예요?\", \"몇 시까지 해요?\") 대화 맥락(직전에 "
            "언급된 서비스명 등)을 반영해 구체적인 문장으로 보강한다. "
            "예: \"가격이 얼마예요?\" + 직전에 \"베란다 청소\" 언급 → "
            "\"베란다 청소 가격이 얼마예요?\". 맥락이 없으면 일반적인 키워드를 "
            "추가한다(예: \"몇 시까지 해요?\" → \"영업시간이 몇 시까지인가요?\")."
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
    sections = [AGENT_SYSTEM_PROMPT_HEADER]
    if not AGENT_PREAMBLE_ENABLED:
        sections.append(NO_AGENT_PREAMBLE_INSTRUCTION)
    sections.append(f"[응답 지시문]\n{response_instructions}")
    return "\n\n".join(sections)


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
    1. 활성 규칙 조회(캐시) + 응답 지시문 조립 (병렬)
    2. tool을 묶은 1차 LLM 스트리밍 호출
    3. tool 호출이 없으면 1차 응답이 곧 최종 답변(이미 스트리밍됨)
    4. tool 호출이 있으면 tool을 직접 실행하고, 그 결과를 다시 LLM에 보내
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

    rules, voice_response_style = await asyncio.gather(
        get_active_rules_async(organization_id),
        get_voice_response_style(organization_id),
    )
    response_instructions = build_response_instructions(
        intent=None,
        knowledge_context=[],
        use_knowledge=False,
        active_task=state.get("active_task"),
        task_step=state.get("task_step"),
        rules=rules,
        channel=channel,
        voice_response_style=voice_response_style,
        should_end_session=False,
    )
    instructions = build_agent_instructions(response_instructions)

    model = await get_streaming_chat_model(organization_id)
    model_with_tools = model.bind_tools(AGENT_TOOLS)

    messages = history_to_messages(conversation_history) + [{"role": "user", "content": user_message}]
    system_and_messages = [{"role": "system", "content": instructions}] + messages

    writer = get_stream_writer()
    tool_call_chunks: dict[int, dict] = {}
    text_chunks: list[str] = []
    has_tool_call = False

    async for chunk in model_with_tools.astream(system_and_messages):
        if chunk.tool_call_chunks:
            if not AGENT_PREAMBLE_ENABLED:
                text_chunks.clear()
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

    if not has_tool_call:
        final_response = "".join(text_chunks).strip()
        if final_response:
            writer({"type": "ai_response_delta", "delta": final_response})
        return {
            "intent": "general",
            "next_action": "respond_general",
            "task_type": "none",
            "use_knowledge": False,
            "should_use_knowledge": False,
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

    if tool_name not in AGENT_TOOLS_BY_NAME:
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
        # 검색된 chunk 내용을 그대로 최종 답변으로 쓴다. 자연스러운 문장
        # 재구성은 포기하지만 LLM round-trip을 1번 줄여 체감 지연을 절반
        # 가까이 낮춘다. 다만 chunk를 전부 이어붙이면(특히 음성 통화에서)
        # 마크다운 헤더나 중복 chunk까지 그대로 다 읽어버려 듣기 힘들어지므로
        # (실측: 494자) 가장 유사도 높은 chunk 1개만, 그것도 일정 길이로
        # 잘라서 쓴다.
        #
        # 1등과 2등 chunk의 유사도 차이가 작으면(서로 다른 서비스 항목이
        # 비슷한 점수로 경쟁 중이라는 뜻) "가격이 얼마예요?"처럼 서비스명을
        # 안 밝힌 모호한 질문일 가능성이 높다 - 둘 다 헤더가 있는 항목형
        # chunk라면(같은 카테고리 안 여러 항목 중 헷갈리는 상황) 임의로
        # 하나를 답하지 않고 어떤 항목인지 되묻는다. 일반 FAQ(영업시간,
        # 정책 등)는 헤더가 없는 단일 chunk라 이 분기를 타지 않는다.
        AMBIGUITY_GAP_THRESHOLD = 0.05
        item_names = []
        if len(chunks) >= 2:
            gap = (chunks[0].get("similarity") or 0) - (chunks[1].get("similarity") or 0)
            if gap < AMBIGUITY_GAP_THRESHOLD:
                for c in chunks[:3]:
                    match = re.search(r"^#{1,6}\s*(?:서비스\s*아이템)\s*:?\s*(\S.*)$", c.get("content") or "", re.MULTILINE)
                    if match:
                        item_names.append(match.group(1).strip())

        if item_names and len(set(item_names)) >= 2:
            direct_message = f"어떤 서비스를 말씀하시는 걸까요? {', '.join(dict.fromkeys(item_names))} 중에서 알려주시면 안내해 드릴게요."
        else:
            direct_message = summarize_knowledge_chunk(chunks[0]) if chunks else None
            if not direct_message:
                direct_message = "확인해보니 관련 정보를 찾지 못했습니다. 담당자에게 다시 확인 후 안내드리겠습니다."
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

        # task_status가 waiting_user_input이면 다음 사용자 메시지도 이
        # 예약을 이어가야 한다 - checkpointer가 영속화하는 active_task/
        # task_step을 계속 켜둔다. 그래야 다음 턴에 LLM이 (혹시 run_task를
        # 다시 호출하지 않더라도) 시스템 프롬프트의 [현재 요청 상태]를 통해
        # "진행 중인 Task가 있다"는 것을 인지한다. completed/failed면 이
        # 턴에서 태스크가 끝난 것이므로 초기화한다.
        task_status = task_result.get("status")
        still_active = task_status == "waiting_user_input"

        return {
            "intent": intent,
            "next_action": next_action,
            "task_type": tool_args.get("task_type", "none"),
            "use_knowledge": False,
            "should_use_knowledge": False,
            "should_end_session": False,
            "active_task": "reservation" if still_active else None,
            "task_step": task_result.get("current_node_key") if still_active else None,
            "task_result": task_result,
            "task_status": task_status,
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
