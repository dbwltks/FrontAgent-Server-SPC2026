from openai import OpenAI

from app.core.config import settings


# OpenAI 클라이언트 생성
# 서버에서만 사용하는 키이므로 프론트엔드에 노출하면 안 된다.
client = OpenAI(api_key=settings.openai_api_key)


def generate_text(
    instructions: str,
    user_message: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """
    OpenAI Responses API로 일반 텍스트 응답을 생성한다.

    conversation_history: [{"role": "user"|"assistant", "content": "..."}] 형태의 이전 대화 목록.
    넘기면 멀티턴 맥락을 유지한다.
    """

    if conversation_history:
        input_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation_history
        ]
        input_messages.append({"role": "user", "content": user_message})
    else:
        input_messages = user_message

    response = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=input_messages,
    )

    return response.output_text