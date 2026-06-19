import re

from app.graph.state import AgentState
from app.rag.retriever import retrieve_knowledge
from app.repositories.knowledge_repo import increment_reference_counts


MAX_KNOWLEDGE_QUERIES = 3
MATCH_COUNT_PER_QUERY = 3


def split_knowledge_queries(user_message: str) -> list[str]:
    """
    사용자 메시지가 여러 지식 질문을 포함하면 하위 질문 배열로 분해한다.

    예:
    "강아지 데려가도 돼? 그리고 프리미엄 청소 얼마야?"
    -> ["강아지 데려가도 돼", "프리미엄 청소 얼마야"]

    주의:
    "에어컨이랑 냉장고 같이 수리 가능해?" 같은 문장은
    하나의 복합 질문일 수 있으므로 '랑', '하고'만으로는 나누지 않는다.
    """
    message = (user_message or "").strip()

    if not message:
        return []

    # 질문을 나누기 쉬운 연결어만 우선 처리한다.
    # "랑", "하고"는 오분해 가능성이 커서 제외한다.
    normalized = re.sub(
        r"\s*(그리고|또|또한|게다가)\s*",
        "\n",
        message,
    )

    # 물음표 또는 줄바꿈 기준으로 분리
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

    if not queries:
        return [message]

    return queries[:MAX_KNOWLEDGE_QUERIES]


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


def knowledge_node(state: AgentState) -> AgentState:
    user_message = state["user_message"]
    organization_id = state["organization_id"]

    knowledge_queries = split_knowledge_queries(user_message)

    if not knowledge_queries:
        knowledge_queries = [user_message]

    knowledge_context_groups: list[dict] = []

    for query in knowledge_queries:
        chunks = retrieve_knowledge(
            organization_id=organization_id,
            query=query,
            match_count=MATCH_COUNT_PER_QUERY,
        )

        knowledge_context_groups.append(
            {
                "query": query,
                "chunks": chunks,
            }
        )

    # 기존 response_node, prompt_builder와의 호환을 위해 전체 chunk 배열도 유지
    knowledge_context = merge_unique_chunks(knowledge_context_groups)

    state["knowledge_queries"] = knowledge_queries
    state["knowledge_context_groups"] = knowledge_context_groups
    state["knowledge_context"] = knowledge_context

    state["used_knowledge"] = [
        {
            "chunk_id": item.get("id"),
            "source_id": item.get("source_id"),
            "source_title": item.get("source_title"),
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
        increment_reference_counts(source_ids)

    return state