from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from app.core.config import settings


client = AsyncOpenAI(api_key=settings.openai_api_key)


async def stream_text(
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    if conversation_history:
        input_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation_history
        ]
        input_messages.append({"role": "user", "content": input_text})
    else:
        input_messages = input_text

    stream = await client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=input_messages,
        stream=True,
    )

    async for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta
