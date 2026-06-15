from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "front-agent-ai-core",
        "app_name": settings.app_name,
        "env": settings.app_env,
        "version": settings.app_version,
    }