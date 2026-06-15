from app.graph.state import AgentState
from app.rag.retriever import retrieve_knowledge


def knowledge_node(state: AgentState) -> AgentState:
    knowledge_context = retrieve_knowledge(
        organization_id=state["organization_id"],
        query=state["user_message"],
        match_count=5,
    )

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

    return state