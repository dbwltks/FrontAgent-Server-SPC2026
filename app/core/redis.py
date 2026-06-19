import redis
from app.core.config import settings


redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
)

# 임베딩 캐시용 — 바이너리 그대로 저장
redis_bytes_client = redis.from_url(
    settings.redis_url,
    decode_responses=False,
)