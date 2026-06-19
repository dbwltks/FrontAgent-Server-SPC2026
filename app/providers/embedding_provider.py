from openai import OpenAI, AsyncOpenAI
from app.core.config import settings


client = OpenAI(api_key=settings.openai_api_key)
async_client = AsyncOpenAI(api_key=settings.openai_api_key)

EMBEDDING_MODEL = "text-embedding-3-small"


async def create_embedding(text: str) -> list[float]:
    response = await async_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )

    return response.data[0].embedding


def create_embeddings_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )

    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]