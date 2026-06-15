from openai import OpenAI
from app.core.config import settings


client = OpenAI(api_key=settings.openai_api_key)


def create_embedding(text: str) -> list[float]:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )

    return response.data[0].embedding