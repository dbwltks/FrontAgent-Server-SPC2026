from collections.abc import Generator

from openai import OpenAI

from app.core.config import settings


# OpenAI 클라이언트 생성
client = OpenAI(api_key=settings.openai_api_key)


def stream_text(
    instructions: str,
    input_text: str,
    conversation_history: list[dict] | None = None,
) -> Generator[str, None, None]:
    """
    OpenAI Responses API를 streaming 모드로 호출한다.

    conversation_history: [{"role": "user"|"assistant", "content": "..."}] 형태의 이전 대화 목록.
    """

    if conversation_history:
        input_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation_history
        ]
        input_messages.append({"role": "user", "content": input_text})
    else:
        input_messages = input_text

    stream = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=input_messages,
        stream=True,
    )

    for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta