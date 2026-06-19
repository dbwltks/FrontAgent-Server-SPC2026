from openai import AsyncOpenAI

from app.core.config import settings


client = AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_text(
    instructions: str,
    user_message: str,
    conversation_history: list[dict] | None = None,
) -> str:
    if conversation_history:
        input_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation_history
        ]
        input_messages.append({"role": "user", "content": user_message})
    else:
        input_messages = user_message

    response = await client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=input_messages,
    )

    return response.output_text