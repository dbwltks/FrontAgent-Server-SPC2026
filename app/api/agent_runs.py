from fastapi import APIRouter, HTTPException

from app.repositories.agent_run_repo import (
    list_agent_runs,
    get_agent_run,
)


router = APIRouter(tags=["Agent Runs"])


@router.get("/agent-runs")
def get_agent_runs(
    organization_id: str,
    session_id: str | None = None,
    limit: int = 50,
):
    """
    Agent 실행 기록 목록을 조회한다.

    관리자 화면에서 최근 AI 응답 기록을 보여줄 때 사용한다.
    session_id를 넘기면 특정 대화의 기록만 조회할 수 있다.
    """

    runs = list_agent_runs(
        organization_id=organization_id,
        session_id=session_id,
        limit=limit,
    )

    return {
        "organization_id": organization_id,
        "session_id": session_id,
        "count": len(runs),
        "items": runs,
    }


@router.get("/agent-runs/{run_id}")
def get_agent_run_detail(
    run_id: str,
    organization_id: str,
):
    """
    Agent 실행 기록 하나를 상세 조회한다.

    어떤 rule과 knowledge가 사용됐는지 확인하는 용도다.
    """

    run = get_agent_run(
        organization_id=organization_id,
        run_id=run_id,
    )

    if not run:
        raise HTTPException(
            status_code=404,
            detail="Agent run not found",
        )

    return run