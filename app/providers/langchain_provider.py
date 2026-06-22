import asyncio
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.repositories.organization_repo import get_organization


@lru_cache(maxsize=128)
def _chat_model_for(provider: str, model: str, streaming: bool) -> ChatOpenAI:
    if provider != "openai":
        raise ValueError(f"지원하지 않는 llm_provider입니다: {provider}")

    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        streaming=streaming,
    )


def _resolve_model_config(organization_id: str) -> tuple[str, str]:
    organization = get_organization(organization_id)

    if not organization:
        return "openai", settings.openai_model

    return (
        organization.get("llm_provider") or "openai",
        organization.get("llm_model") or settings.openai_model,
    )


async def get_chat_model(organization_id: str) -> ChatOpenAI:
    provider, model = await asyncio.to_thread(_resolve_model_config, organization_id)
    return _chat_model_for(provider, model, streaming=False)


async def get_streaming_chat_model(organization_id: str) -> ChatOpenAI:
    provider, model = await asyncio.to_thread(_resolve_model_config, organization_id)
    return _chat_model_for(provider, model, streaming=True)


def _history_to_messages(conversation_history: list[dict] | None) -> list:
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
            "history": _history_to_messages(conversation_history),
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
            "history": _history_to_messages(conversation_history),
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
    model = await get_chat_model(organization_id)
    structured_model = model.with_structured_output(schema)
    chain = _PROMPT | structured_model

    if postprocess is not None:
        chain = chain | postprocess

    return await chain.ainvoke(
        {
            "instructions": instructions,
            "history": _history_to_messages(conversation_history),
            "user_message": user_message,
        }
    )
