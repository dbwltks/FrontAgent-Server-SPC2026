from collections.abc import Generator

from openai import OpenAI

from app.core.config import settings


# OpenAI 클라이언트 생성
client = OpenAI(api_key=settings.openai_api_key)


def stream_text(
    instructions: str,
    input_text: str,
) -> Generator[str, None, None]:
    """
    OpenAI Responses API를 streaming 모드로 호출한다.

    역할:
    - AI 응답을 한 번에 받지 않는다.
    - 생성되는 텍스트 조각(delta)을 순서대로 yield 한다.
    """

    stream = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=input_text,
        stream=True,
    )

    for event in stream:
        # 실제 텍스트 조각 이벤트만 사용한다.
        if event.type == "response.output_text.delta":
            yield event.delta