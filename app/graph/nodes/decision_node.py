from app.graph.nodes.knowledge_node import normalize_knowledge_queries


def _normalize_knowledge_queries(
    knowledge_queries: list[str] | None,
    *,
    use_knowledge: bool,
    user_message: str,
) -> list[str]:
    if not use_knowledge:
        return []
    return normalize_knowledge_queries(user_message, knowledge_queries or [])
