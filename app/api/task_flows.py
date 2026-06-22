from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.tasks.runner import DynamicTaskRunner


router = APIRouter(prefix="/task-flows", tags=["Task Flows"])


class TaskFlowTestRequest(BaseModel):
    organization_id: str = Field(..., example="00000000-0000-0000-0000-000000000000")
    session_id: str = Field(..., example="task_test_001")
    message: str = Field(..., example="예약 시작")


@router.post("/{flow_id}/test")
def test_task_flow(flow_id: str, request: TaskFlowTestRequest):
    runner = DynamicTaskRunner()

    result = runner.run(
        organization_id=request.organization_id,
        session_id=request.session_id,
        user_message=request.message,
        flow_id=flow_id,
    )

    return asdict(result)