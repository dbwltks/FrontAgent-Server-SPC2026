from fastapi import FastAPI

from app.core.config import settings
from app.graph.graph_runtime import lifespan_graph
from app.api import (
    health,
    chat,
    knowledge,
    knowledge_folders,
    agent_runs,
    conversations,
    rules,
    task_flows,
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan_graph,
)

# 기본 상태 확인 API
app.include_router(health.router)

# 채팅 API (SSE 스트리밍 지원, 웹/전화/웹콜 등 모든 채널 공통)
app.include_router(chat.router)

# AI 응답 규칙 관리 API
app.include_router(rules.router)

# 지식 폴더 관리 API
app.include_router(knowledge_folders.router)

# 지식 관리 API
app.include_router(knowledge.router)

# Agent 실행 로그 API
app.include_router(agent_runs.router)

# 상담방 / 메시지 관리 API
app.include_router(conversations.router)

# 태스크 플로우 테스트 API
app.include_router(task_flows.router)