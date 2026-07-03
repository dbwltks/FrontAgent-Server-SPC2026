import asyncio
import logging
from contextlib import asynccontextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings
from app.graph.checkpoint_state import slim_channel_values_for_checkpoint
from app.graph.graph import build_graph
from app.repositories.conversation_repo import close_idle_calls, warn_or_close_idle_chats


logger = logging.getLogger(__name__)

_connection_pool: AsyncConnectionPool | None = None
agent_graph = None

_IDLE_SWEEP_INTERVAL_SECONDS = 10
_IDLE_CALL_MINUTES = 1

_CHECKPOINT_TUPLE_CACHE_MAX = 2048


class CachedAsyncPostgresSaver(AsyncPostgresSaver):
    """
    동일 워커에서 연속 턴이 이어질 때 Postgres checkpoint READ를 생략한다.
    aput 직후 최신 tuple을 캐시해 다음 aget_tuple에서 사용한다.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tuple_cache: dict[tuple[str, str], CheckpointTuple | None] = {}

    @staticmethod
    def _cache_key(config: RunnableConfig) -> tuple[str, str]:
        configurable = config.get("configurable") or {}
        return (
            str(configurable.get("thread_id") or ""),
            str(configurable.get("checkpoint_ns") or ""),
        )

    def _remember_tuple(self, key: tuple[str, str], checkpoint_tuple: CheckpointTuple | None) -> None:
        if not key[0]:
            return
        if len(self._tuple_cache) >= _CHECKPOINT_TUPLE_CACHE_MAX:
            self._tuple_cache.pop(next(iter(self._tuple_cache)))
        self._tuple_cache[key] = checkpoint_tuple

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        key = self._cache_key(config)
        if key in self._tuple_cache:
            return self._tuple_cache[key]

        checkpoint_tuple = await super().aget_tuple(config)
        self._remember_tuple(key, checkpoint_tuple)
        return checkpoint_tuple

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        channel_values = checkpoint.get("channel_values")
        if isinstance(channel_values, dict):
            checkpoint = {
                **checkpoint,
                "channel_values": slim_channel_values_for_checkpoint(channel_values),
            }

        next_config = await super().aput(config, checkpoint, metadata, new_versions)
        key = self._cache_key(config)
        self._remember_tuple(
            key,
            CheckpointTuple(
                config=next_config,
                checkpoint=checkpoint,
                metadata=metadata,
                pending_writes=[],
            ),
        )
        return next_config

    async def adelete_thread(self, thread_id: str) -> None:
        await super().adelete_thread(thread_id)
        stale_keys = [key for key in self._tuple_cache if key[0] == thread_id]
        for key in stale_keys:
            self._tuple_cache.pop(key, None)


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


async def _warm_up_external_clients() -> None:
    """
    LLM과 같은 이유로, 지식검색 경로에서만 쓰이는 외부 연동(임베딩 API,
    Supabase RPC, Redis)도 첫 실제 호출 때 TLS 핸드셰이크/connection 초기화
    비용이 든다. 응답형 일반 대화는 LLM 워밍업만으로 체감 콜드스타트가
    거의 사라졌지만, 지식검색 첫 요청은 이 셋이 워밍업되지 않아 여전히
    수 초가 더 걸렸다(실측) - 서버 기동 시점에 미리 치른다.
    """
    from app.core.db import supabase
    from app.core.redis import redis_bytes_client
    from app.providers.embedding_provider import async_client as embedding_client

    async def _warm_up_embedding() -> None:
        try:
            await embedding_client.embeddings.create(model="text-embedding-3-small", input="ping")
        except Exception:
            logger.warning("embedding warm-up failed", exc_info=True)

    async def _warm_up_supabase() -> None:
        try:
            await asyncio.to_thread(lambda: supabase.table("organizations").select("id").limit(1).execute())
        except Exception:
            logger.warning("supabase warm-up failed", exc_info=True)

    async def _warm_up_redis() -> None:
        try:
            await asyncio.to_thread(redis_bytes_client.ping)
        except Exception:
            logger.warning("redis warm-up failed", exc_info=True)

    await asyncio.gather(_warm_up_embedding(), _warm_up_supabase(), _warm_up_redis())


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

    checkpointer = CachedAsyncPostgresSaver(conn=_connection_pool)
    await checkpointer.setup()

    agent_graph = build_graph(checkpointer=checkpointer)
    sweep_task = asyncio.create_task(_sweep_idle_conversations())
    # 두 워밍업을 asyncio.gather로 동시에 돌리면(같은 프로세스에서 OpenAI로
    # 향하는 TLS 핸드셰이크가 동시에 여러 개 발생) 워밍업 자체도 느려지고
    # 그 직후 첫 실제 요청까지 같이 느려지는 경합이 실측됐다 - 순차 실행한다.
    await _warm_up_llm_clients()
    await _warm_up_external_clients()

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
        # task_result/active_task/task_step은 checkpointer가 이전 턴 값을
        # 유지한다. task memory(variables)는 task_sessions(Redis/DB)만 source of truth.

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


# super-step마다 checkpoint write하지 않고 그래프 종료 시 1회만 저장한다.
# 채팅/음성 상담은 중간 크래시 복구보다 응답 지연이 더 중요하다.
GRAPH_DURABILITY = "exit"


def graph_execution_kwargs() -> dict:
    """ainvoke/astream 공통 durability 옵션."""
    return {"durability": GRAPH_DURABILITY}
