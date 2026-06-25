from app.core.db import supabase
from app.repositories.conversation_repo import get_conversation_by_session


def create_agent_run(
    organization_id: str,
    session_id: str,
    user_message: str,
    intent: str | None,
    applied_rules: list,
    used_knowledge: list,
    final_response: str | None,
    status: str = "success",
    error_message: str | None = None,
) -> dict | None:
    """
    AI Agent가 한 번 실행될 때마다 실행 기록을 저장한다.

    저장하는 내용:
    - 어떤 조직에서 실행됐는지
    - 어떤 세션인지
    - 사용자가 뭐라고 질문했는지
    - intent가 무엇으로 분류됐는지
    - 어떤 rule이 적용됐는지
    - 어떤 knowledge를 참고했는지
    - 최종 응답이 무엇인지
    """

    result = (
        supabase.table("agent_runs")
        .insert(
            {
                "organization_id": organization_id,
                "session_id": session_id,
                "user_message": user_message,
                "intent": intent,
                "applied_rules": applied_rules,
                "used_knowledge": used_knowledge,
                "final_response": final_response,
                "status": status,
                "error_message": error_message,
            }
        )
        .execute()
    )

    if not result.data:
        return None

    return result.data[0]


def list_agent_runs(
    organization_id: str,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    관리자 화면에서 AI 실행 기록 목록을 조회할 때 사용한다.

    session_id가 있으면 특정 대화방 기록만 조회하고,
    없으면 조직 전체 최근 기록을 조회한다.

    각 run의 session_id로 conversations.channel을 조인해 채워준다
    (관리자 화면이 채널별로 묶어 보여주기 위해 필요).
    """

    query = (
        supabase.table("agent_runs")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if session_id:
        query = query.eq("session_id", session_id)

    result = query.execute()
    runs = result.data or []

    session_ids = list({run["session_id"] for run in runs if run.get("session_id")})
    channel_by_session: dict[str, str] = {}
    if session_ids:
        conversations = (
            supabase.table("conversations")
            .select("session_id,channel")
            .eq("organization_id", organization_id)
            .in_("session_id", session_ids)
            .execute()
        )
        channel_by_session = {
            row["session_id"]: row["channel"] for row in (conversations.data or [])
        }

    for run in runs:
        run["channel"] = channel_by_session.get(run.get("session_id"))

    return runs


def get_agent_run(
    organization_id: str,
    run_id: str,
) -> dict | None:
    """
    특정 Agent Run 하나의 상세 기록을 조회한다.
    """

    result = (
        supabase.table("agent_runs")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", run_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        return None

    run = result.data[0]

    conversation = get_conversation_by_session(
        organization_id=organization_id,
        session_id=run["session_id"],
    )
    run["channel"] = conversation.get("channel") if conversation else None

    return run