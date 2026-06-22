import asyncio
import logging
import re

from app.graph.state import AgentState
from app.rag.retriever import retrieve_knowledge
from app.repositories.knowledge_repo import increment_reference_counts


logger = logging.getLogger(__name__)


MAX_KNOWLEDGE_QUERIES = 3
MATCH_COUNT_PER_QUERY = 3


def fallback_split_knowledge_queries(user_message: str) -> list[str]:
    """
    LLM 질문 분해가 실패했을 때만 사용하는 최소 fallback.
    업종별 키워드가 아니라 명확한 연결어/구분자만 사용한다.
    """
    message = (user_message or "").strip()

    if not message:
        return []

    normalized = re.sub(
        r"\s*(그리고|또|또한|게다가)\s*",
        "\n",
        message,
    )

    raw_parts = re.split(r"[?\n]+", normalized)

    queries: list[str] = []

    for part in raw_parts:
        query = part.strip(" \t\r\n.!,")

        if not query:
            continue

        if len(query) < 2:
            continue

        if query not in queries:
            queries.append(query)

        if len(queries) >= MAX_KNOWLEDGE_QUERIES:
            break

    return queries or [message]


def merge_unique_chunks(knowledge_context_groups: list[dict]) -> list[dict]:
    """
    질문별 검색 결과를 기존 knowledge_context 구조와 호환되도록 하나의 배열로 합친다.
    같은 chunk가 여러 질문에서 중복 검색될 수 있으므로 id 기준으로 중복 제거한다.
    """
    merged: list[dict] = []
    seen_keys: set[str] = set()

    for group in knowledge_context_groups:
        chunks = group.get("chunks", [])

        for item in chunks:
            chunk_id = item.get("id")
            source_id = item.get("source_id")
            content = item.get("content")

            key = str(chunk_id or f"{source_id}:{content}")

            if key in seen_keys:
                continue

            seen_keys.add(key)
            merged.append(item)

    return merged


async def knowledge_node(state: AgentState) -> AgentState:
    """
    질문 분해는 decision_node가 intent 분류와 함께 이미 수행해
    state["knowledge_queries"]에 채워준다.
    decision_node가 비어 있는 채로 넘기거나(use_knowledge=False 등) 호출되지 않은
    경로(예: 기존 그래프 테스트)에서는 fallback으로 최소 분해를 시도한다.
    """
    user_message = state["user_message"]
    organization_id = state["organization_id"]
    knowledge_folder_id = state.get("knowledge_folder_id")

    knowledge_queries = state.get("knowledge_queries") or fallback_split_knowledge_queries(user_message)

    # 질문 하나의 검색(임베딩 API/Supabase RPC) 실패가 다른 질문 결과까지 막지 않게 한다.
    results = await asyncio.gather(
        *[
            retrieve_knowledge(
                organization_id=organization_id,
                query=query,
                match_count=MATCH_COUNT_PER_QUERY,
                folder_id=knowledge_folder_id,
            )
            for query in knowledge_queries
        ],
        return_exceptions=True,
    )

    knowledge_context_groups = []

    for query, result in zip(knowledge_queries, results):
        if isinstance(result, Exception):
            logger.warning("knowledge retrieval failed for query=%r", query, exc_info=result)
            knowledge_context_groups.append({"query": query, "chunks": []})
        else:
            knowledge_context_groups.append({"query": query, "chunks": result})

    knowledge_context = merge_unique_chunks(knowledge_context_groups)

    state["knowledge_queries"] = knowledge_queries
    state["knowledge_context_groups"] = knowledge_context_groups
    state["knowledge_context"] = knowledge_context
    state["used_knowledge"] = [
        {
            "chunk_id": item.get("id"),
            "source_id": item.get("source_id"),
            "source_title": item.get("source_title"),
            "folder_id": item.get("folder_id"),
            "similarity": item.get("similarity"),
        }
        for item in knowledge_context
    ]

    source_ids = list(
        {
            item.get("source_id")
            for item in knowledge_context
            if item.get("source_id")
        }
    )

    if source_ids:
        try:
            await asyncio.to_thread(increment_reference_counts, source_ids)
        except Exception:
            logger.warning("failed to increment knowledge reference counts", exc_info=True)

    return state
