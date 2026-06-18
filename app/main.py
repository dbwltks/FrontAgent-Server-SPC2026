from fastapi import FastAPI

from app.core.config import settings
from app.api import health, chat, knowledge, agent_runs, conversations, ws_chat, rules


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

# 기본 상태 확인 API
app.include_router(health.router)

# HTTP 채팅 API
app.include_router(chat.router)

# 새로 추가 26.06.17 밤
# AI 응답 규칙 관리 API
app.include_router(rules.router)

# 지식 관리 API
app.include_router(knowledge.router)

# Agent 실행 로그 API
app.include_router(agent_runs.router)

# 상담방 / 메시지 관리 API
app.include_router(conversations.router)

# 사용자 채팅 WebSocket API
app.include_router(ws_chat.router)


