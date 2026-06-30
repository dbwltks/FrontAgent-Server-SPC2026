import asyncio
import logging

from app.graph.nodes.agent_node import agent_node
from app.graph.nodes.conversation_node import conversation_node
from app.graph.state import AgentState


logger = logging.getLogger(__name__)


async def prepare_node(state: AgentState) -> dict:
    """
    conversation_node(상담방 조회/생성, 고객 메시지 저장)와 agent_node(Main LLM
    + tool calling으로 의도 판단/지식검색/예약/답변 생성을 한 번에 처리)를
    동시에 실행한다.

    둘은 서로 의존하지 않는다 - conversation_node가 새로 만드는 값(conversation_id,
    ai_enabled)을 agent_node는 읽지 않는다. 진행 중 예약 등 태스크 상태는
    checkpointer가 이전 턴부터 이미 복원해둔 state 값(active_task/task_step)을
    그대로 쓰고, agent_node가 run_task tool을 부르면 그 안에서 TaskRepository가
    현재 태스크 세션을 다시 조회한다. 그래서 conversation_node의 DB round-trip과
    agent_node의 RAG+LLM(+필요시 tool) 호출을 asyncio.gather로 동시에 진행해
    둘 중 더 오래 걸리는 쪽 시간만 낸다.

    ai_enabled가 꺼져 있으면 agent_node 결과(이미 LLM/tool을 호출해버렸어도)는
    버리고 conversation 결과만 반환한다 - ai_handoff_node가 관리자 응답 대기로
    전환한다. ai_enabled는 거의 항상 켜져 있으므로(관리자가 수동으로 끈 상담방만
    예외) 이 트레이드오프로 버려지는 호출은 드물다.
    """
    conversation_update, agent_update = await asyncio.gather(
        asyncio.to_thread(conversation_node, state),
        agent_node(state),
    )

    if not conversation_update.get("ai_enabled", True):
        return conversation_update

    return {**conversation_update, **agent_update}
