import asyncio
import logging
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings
from app.graph.graph import build_graph
from app.repositories.conversation_repo import close_idle_calls, warn_or_close_idle_chats


logger = logging.getLogger(__name__)

_connection_pool: AsyncConnectionPool | None = None
agent_graph = None

_IDLE_SWEEP_INTERVAL_SECONDS = 10
_IDLE_CALL_MINUTES = 1


async def _warm_up_llm_clients() -> None:
    """
    조직별로 흔히 쓰이는 모델 조합(응답용 모델, streaming 여부)의 ChatOpenAI
    인스턴스를 앱 시작 시점에 미리 만들고 더미 호출을 한 번 날려둔다.

    실측 결과 ChatOpenAI() 생성자 자체는 항상 ~0.3초로 일정한데, 같은
    프로세스에서 특정 모델로 처음 실제 API 호출을 보낼 때만 1.5~2초가
    추가로 걸렸다(TLS 핸드셰이크/connection pool 초기화로 추정 - OpenAI도
    새 connection마다 TLS 재협상 비용이 있다고 명시한다). 이 비용을 유저
    요청이 아니라 서버 기동 시점에 미리 치르게 한다. 워밍업이 실패해도
    (네트워크 일시 장애 등) 첫 실제 요청이 느려질 뿐 서비스 자체는 정상
    동작해야 하므로 예외를 삼킨다.
    """
    from app.providers.langchain_provider import _chat_model_for

    model_combos = [
        (settings.openai_model, False),
        (settings.openai_model, True),
    ]

    async def _warm_up_chat_model(model_name: str, streaming: bool) -> None:
        try:
            model = _chat_model_for("openai", model_name, streaming)
            await model.ainvoke("ping")
        except Exception:
            logger.warning("LLM warm-up failed for model=%s streaming=%s", model_name, streaming, exc_info=True)

    await asyncio.gather(*[_warm_up_chat_model(name, streaming) for name, streaming in model_combos])


async def _sweep_idle_conversations() -> None:
    """
    채팅 무응답 안내/종료, 통화 무응답 종료(클라이언트가 /voice/call/end를
    못 호출한 경우의 안전망)를 주기적으로 처리한다.
    """
    while True:
        try:
            await asyncio.to_thread(warn_or_close_idle_chats)
            await asyncio.to_thread(close_idle_calls, _IDLE_CALL_MINUTES)
        except Exception:
            logger.exception("idle conversation sweep failed")

        await asyncio.sleep(_IDLE_SWEEP_INTERVAL_SECONDS)




@asynccontextmanager
async def lifespan_graph(_app=None):
    """
    FastAPI lifespan에서 사용한다.
    AsyncPostgresSaver checkpointer를 앱 생명주기 동안 열어두고,
    그 checkpointer로 컴파일된 agent_graph를 모듈 전역에 노출한다.
    동시에 무응답 상담방/통화를 정리하는 백그라운드 루프를 띄운다.

    checkpointer는 단일 AsyncConnection 대신 AsyncConnectionPool을 쓴다.
    AsyncPostgresSaver 내부 _cursor()가 매 호출마다 self.lock(단일
    asyncio.Lock)을 잡으므로 같은 요청 안에서의 순차 노드 전환 자체는
    pool 전환으로 빨라지지 않지만, 동시 세션(여러 통화/채팅)이 겹칠 때
    connection 재획득 비용과 lock 대기를 줄여준다.
    """
    global _connection_pool, agent_graph

    _connection_pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=2,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await _connection_pool.open()

    checkpointer = AsyncPostgresSaver(conn=_connection_pool)
    await checkpointer.setup()

    agent_graph = build_graph(checkpointer=checkpointer)
    sweep_task = asyncio.create_task(_sweep_idle_conversations())
    await _warm_up_llm_clients()

    try:
        yield
    finally:
        sweep_task.cancel()
        await _connection_pool.close()
        _connection_pool = None
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
    channel: str = "web_chat",
    log_message: str | None = None,
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
        "log_message": log_message,
        "channel": channel,

        "pending_task_prompt": None,
        "follow_up_response": None,
        "conversation_id": None,
        "ai_enabled": True,

        # agent_node 결과
        "intent": None,
        "next_action": None,
        "task_type": None,
        "use_knowledge": False,
        "decision_reason": None,
        "should_end_session": False,
        "task_result": None,

        # 기존 should_use_knowledge_node와의 호환용
        "should_use_knowledge": False,

        # rules
        "rules": [],
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
