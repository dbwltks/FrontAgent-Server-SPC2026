from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings
from app.graph.graph import build_graph


_checkpointer_cm = None
agent_graph = None


@asynccontextmanager
async def lifespan_graph(_app=None):
    """
    FastAPI lifespan에서 사용한다.
    AsyncPostgresSaver 커넥션 풀을 앱 생명주기 동안 열어두고,
    그 checkpointer로 컴파일된 agent_graph를 모듈 전역에 노출한다.
    """
    global _checkpointer_cm, agent_graph

    _checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.database_url)
    checkpointer = await _checkpointer_cm.__aenter__()
    await checkpointer.setup()

    agent_graph = build_graph(checkpointer=checkpointer)

    try:
        yield
    finally:
        await _checkpointer_cm.__aexit__(None, None, None)
        agent_graph = None


def get_agent_graph():
    if agent_graph is None:
        raise RuntimeError(
            "agent_graph가 아직 초기화되지 않았습니다. "
            "FastAPI lifespan(lifespan_graph)이 먼저 실행되어야 합니다."
        )
    return agent_graph


def thread_id_for(organization_id: str, session_id: str) -> str:
    """
    checkpointer가 멀티턴 메모리를 구분하는 단위인 thread_id를 만든다.
    기존 Redis 세션 키 패턴과 동일한 네임스페이스를 사용한다.
    """
    return f"{organization_id}:{session_id}"


def build_initial_state(
    organization_id: str,
    session_id: str,
    user_message: str,
    knowledge_folder_id: str | None = None,
) -> dict:
    """
    /chat이 모든 채널(웹/전화/웹콜) 공통으로 사용하는 초기 AgentState를 만든다.
    messages는 checkpointer가 thread_id 기준으로 이전 턴 state를 복원해 채워주므로
    여기서는 넣지 않는다 (conversation_node가 이번 턴 사용자 메시지만 추가한다).
    """
    return {
        "organization_id": organization_id,
        "session_id": session_id,
        "user_message": user_message,
        "conversation_id": None,
        "ai_enabled": True,

        # decision_node 결과
        "intent": None,
        "next_action": None,
        "task_type": None,
        "use_knowledge": False,
        "decision_reason": None,
        "task_result": None,

        # 기존 should_use_knowledge_node와의 호환용
        "should_use_knowledge": False,

        # rules
        "rules": [],
        "rule_instructions": "",
        "applied_rules": [],

        # knowledge
        "knowledge_folder_id": knowledge_folder_id,
        "knowledge_context": [],
        "used_knowledge": [],

        # final response
        "final_response": None,
    }


def graph_config_for(organization_id: str, session_id: str) -> dict:
    """
    agent_graph.ainvoke/astream에 넘기는 config를 만든다.
    """
    return {
        "configurable": {
            "thread_id": thread_id_for(organization_id, session_id),
        }
    }
