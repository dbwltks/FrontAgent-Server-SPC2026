import asyncio
import json
import re

from app.graph.state import AgentState
from app.providers.openai_provider import generate_text
from app.rag.retriever import retrieve_knowledge
from app.repositories.knowledge_repo import increment_reference_counts


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


def parse_json_array(text: str) -> list[str]:
    """
    LLM 응답에서 JSON 배열만 안전하게 추출한다.
    """
    if not text:
        return []

    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", cleaned)

        if not match:
            return []

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    if not isinstance(data, list):
        return []

    queries: list[str] = []

    for item in data:
        if not isinstance(item, str):
            continue

        query = item.strip()

        if not query:
            continue

        if len(query) < 2:
            continue

        if query not in queries:
            queries.append(query)

        if len(queries) >= MAX_KNOWLEDGE_QUERIES:
            break

    return queries


async def split_knowledge_queries(user_message: str) -> list[str]:
    message = (user_message or "").strip()

    if not message:
        return []

    instructions = f"""
너는 사용자 메시지를 지식 검색용 질문 배열로 분해하는 분류기다.

목표:
- 사용자가 여러 정보를 한 번에 물어보면 서로 다른 정보 문의를 최대 {MAX_KNOWLEDGE_QUERIES}개까지 분리한다.
- 각 항목은 knowledge 검색에 바로 사용할 수 있는 짧고 명확한 한국어 질문으로 만든다.
- 예약 실행 요청, 예약 변경 요청, 예약 취소 요청, 상담사 연결 요청, 단순 인사는 지식 검색 질문으로 만들지 않는다.
- 정보 문의와 예약 요청이 섞여 있으면 정보 문의만 배열에 담는다.
- 같은 의미의 질문은 중복 제거한다.
- 출력은 반드시 JSON 배열만 반환한다.
- 설명 문장, markdown, 코드블록은 절대 붙이지 않는다.

예시 1:
입력: "강아지 데려가도 돼? 그리고 프리미엄 청소 가격도 알려줘, 하계 학술대회 정보도"
출력: ["강아지 데려가도 돼?", "프리미엄 청소 가격 알려줘", "하계 학술대회 정보 알려줘"]

예시 2:
입력: "프리미엄 청소 얼마야? 그리고 내일 예약하고 싶어"
출력: ["프리미엄 청소 가격 알려줘"]

예시 3:
입력: "내일 오후 3시에 이상욱 이름으로 예약해줘"
출력: []

예시 4:
입력: "안녕하세요"
출력: []

사용자 메시지:
{message}
""".strip()

    try:
        llm_result = await generate_text(
            instructions=instructions,
            user_message=message,
        )

        queries = parse_json_array(llm_result)

        if queries:
            return queries[:MAX_KNOWLEDGE_QUERIES]

        return fallback_split_knowledge_queries(message)

    except Exception:
        return fallback_split_knowledge_queries(message)


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
    user_message = state["user_message"]
    organization_id = state["organization_id"]
    knowledge_folder_id = state.get("knowledge_folder_id")

    knowledge_queries = await split_knowledge_queries(user_message)

    results = await asyncio.gather(
        *[
            retrieve_knowledge(
                organization_id=organization_id,
                query=query,
                match_count=MATCH_COUNT_PER_QUERY,
                folder_id=knowledge_folder_id,
            )
            for query in knowledge_queries
        ]
    )

    knowledge_context_groups = [
        {"query": query, "chunks": chunks}
        for query, chunks in zip(knowledge_queries, results)
    ]

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
        increment_reference_counts(source_ids)

    return state