import json

from openai import OpenAI, AsyncOpenAI
from app.core.config import settings
from app.core.redis import redis_bytes_client


client = OpenAI(api_key=settings.openai_api_key)
async_client = AsyncOpenAI(api_key=settings.openai_api_key)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_CACHE_TTL = 60 * 60 * 24  # 24시간


async def create_embedding(text: str) -> list[float]:
    cache_key = f"emb:{EMBEDDING_MODEL}:{text}"

    cached = redis_bytes_client.get(cache_key)
    if cached:
        return json.loads(cached)

    response = await async_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )

    embedding = response.data[0].embedding
    redis_bytes_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))

    return embedding


def create_embeddings_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )

    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]