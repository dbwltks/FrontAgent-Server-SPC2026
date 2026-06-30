import asyncio
import time
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.repositories.organization_repo import get_organization


# organizations 설정은 자주 바뀌지 않으므로 매 LLM 호출마다 DB를 왕복하지 않게
# 짧은 TTL로 캐싱한다. 모델/provider를 바꾼 뒤에는 최대 TTL만큼 지연 반영된다.
_ORGANIZATION_CACHE_TTL_SECONDS = 60
_organization_cache: dict[str, tuple[float, dict]] = {}


@lru_cache(maxsize=128)
def _chat_model_for(provider: str, model: str, streaming: bool) -> ChatOpenAI:
    if provider != "openai":
        raise ValueError(f"지원하지 않는 llm_provider입니다: {provider}")

    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        streaming=streaming,
    )


def _resolve_organization_config(organization_id: str) -> dict:
    cached = _organization_cache.get(organization_id)
    now = time.monotonic()

    if cached is not None and now - cached[0] < _ORGANIZATION_CACHE_TTL_SECONDS:
        return cached[1]

    organization = get_organization(organization_id) or {}
    provider = organization.get("llm_provider") or "openai"
    model = organization.get("llm_model") or settings.openai_model

    config = {
        "provider": provider,
        "model": model,
        # decision_model이 비어 있으면 응답용 모델(gpt-4.1-mini 등 더 무거운
        # 모델)이 아니라 가벼운 기본 모델(settings.decision_model)로 분류한다.
        # intent 분류 + direct_answer 생성을 LLM 1번에 묶었기 때문에, 이 호출이
        # 느리면 일반 대화 전체가 느려진다.
        "decision_model": organization.get("decision_model") or settings.decision_model,
        "voice_response_style": organization.get("voice_response_style", "friendly_short"),
    }

    _organization_cache[organization_id] = (now, config)
    return config


async def get_chat_model(organization_id: str) -> ChatOpenAI:
    config = await asyncio.to_thread(_resolve_organization_config, organization_id)
    return _chat_model_for(config["provider"], config["model"], streaming=False)


async def get_streaming_chat_model(organization_id: str) -> ChatOpenAI:
    config = await asyncio.to_thread(_resolve_organization_config, organization_id)
    return _chat_model_for(config["provider"], config["model"], streaming=True)


async def get_decision_chat_model(organization_id: str) -> ChatOpenAI:
    """
    intent 분류(decision_node)는 단순 분류 작업이라 응답 생성용 모델보다
    가볍고 빠른 모델(organizations.decision_model)을 쓸 수 있게 분리한다.
    """
    config = await asyncio.to_thread(_resolve_organization_config, organization_id)
    return _chat_model_for(config["provider"], config["decision_model"], streaming=False)


async def get_voice_response_style(organization_id: str) -> str:
    """
    response_node가 매 턴마다 organization_ai_settings를 별도로 조회하지 않도록,
    이미 60초 TTL로 캐시된 organization 설정에서 응답 말투만 꺼내준다.
    """
    config = await asyncio.to_thread(_resolve_organization_config, organization_id)
    return config["voice_response_style"]


def history_to_messages(conversation_history: list[dict] | None) -> list:
    if not conversation_history:
        return []

    messages = []

    for msg in conversation_history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    return messages


_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "{instructions}"),
        MessagesPlaceholder("history"),
        ("human", "{user_message}"),
    ]
)


async def build_chain(
    organization_id: str,
    parser: RunnableLambda | None = None,
    streaming: bool = False,
):
    """
    instructions/history/user_message를 받아 LLM을 호출하는 LCEL 체인을 만든다.
    organization_id로 그 조직에 설정된 provider/model을 가져와 쓴다.
    parser를 넘기면 모델 응답(AIMessage)을 RunnableLambda로 가공한 결과를 반환하고,
    넘기지 않으면 AIMessage를 그대로 반환한다.
    """
    model = (
        await get_streaming_chat_model(organization_id)
        if streaming
        else await get_chat_model(organization_id)
    )
    chain = _PROMPT | model

    if parser is not None:
        chain = chain | parser

    return chain


async def generate_text(
    organization_id: str,
    instructions: str,
    user_message: str,
    conversation_history: list[dict] | None = None,
    parser: RunnableLambda | None = None,
):
    """
    decision_node처럼 한 번에 결과를 받는 비스트리밍 호출.
    parser가 있으면 그 결과(예: dict)를, 없으면 텍스트(str)를 반환한다.
    """
    chain = await build_chain(organization_id, parser=parser, streaming=False)

    result = await chain.ainvoke(
        {
            "instructions": instructions,
            "history": history_to_messages(conversation_history),
            "user_message": user_message,
        }
    )

    if parser is not None:
        return result

    return result.content


async def stream_text(
    organization_id: str,
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None = None,
):
    """
    response_node가 쓰는 토큰 단위 스트리밍 호출.
    """
    chain = await build_chain(organization_id, streaming=True)

    async for chunk in chain.astream(
        {
            "instructions": instructions,
            "history": history_to_messages(conversation_history),
            "user_message": input_text,
        }
    ):
        if chunk.content:
            yield chunk.content


async def generate_structured(
    organization_id: str,
    instructions: str,
    user_message: str,
    schema: type,
    conversation_history: list[dict] | None = None,
    postprocess: RunnableLambda | None = None,
):
    """
    OpenAI native structured output(model.with_structured_output)으로
    LLM 응답을 곧바로 schema(Pydantic 모델) 인스턴스로 받는다.
    JSON 파싱 실패 자체가 일어나지 않으므로 decision_node 같은 분류 노드에서
    프롬프트에 JSON 형식을 적어주고 직접 json.loads하던 방식을 대체한다.

    postprocess를 넘기면 그 RunnableLambda로 schema 인스턴스를 추가 가공한다
    (예: 정규식 fallback으로 knowledge_queries를 보강).
    """
    model = await get_decision_chat_model(organization_id)
    structured_model = model.with_structured_output(schema)
    chain = _PROMPT | structured_model

    if postprocess is not None:
        chain = chain | postprocess

    return await chain.ainvoke(
        {
            "instructions": instructions,
            "history": history_to_messages(conversation_history),
            "user_message": user_message,
        }
    )
