from openai import OpenAI

from app.core.config import settings


# OpenAI 클라이언트 생성
# 서버에서만 사용하는 키이므로 프론트엔드에 노출하면 안 된다.
client = OpenAI(api_key=settings.openai_api_key)


def generate_text(
    instructions: str,
    user_message: str,
) -> str:
    """
    OpenAI Responses API로 일반 텍스트 응답을 생성한다.

    이 함수는 스트리밍이 아닌 일반 /chat 또는 agent_graph.invoke에서 사용한다.
    """

    response = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=user_message,
    )

    return response.output_text