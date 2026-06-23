from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from supabase import Client, create_client

from app.core.config import settings
from app.tasks.runner import DynamicTaskRunner


router = APIRouter(prefix="/task-flows", tags=["Task Flows"])


def get_supabase_client() -> Client:
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )


class TaskFlowCreateRequest(BaseModel):
    organization_id: str = Field(..., example="00000000-0000-0000-0000-000000000000")

    name: str = Field(..., example="예약 생성 플로우")
    description: str | None = Field(None, example="고객이 새 예약을 요청할 때 실행되는 플로우")

    trigger_intent: str | None = Field(None, example="reservation_create")
    trigger_description: str | None = Field(None, example="고객이 새 예약을 원할 때 실행")
    trigger_examples: list[str] = Field(default_factory=list)

    allowed_channels: list[str] = Field(default_factory=lambda: ["chat", "voice"])
    filters: dict[str, Any] = Field(default_factory=dict)

    is_enabled: bool = True


class TaskFlowUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None

    trigger_intent: str | None = None
    trigger_description: str | None = None
    trigger_examples: list[str] | None = None

    allowed_channels: list[str] | None = None
    filters: dict[str, Any] | None = None

    is_enabled: bool | None = None


class TaskFlowTestRequest(BaseModel):
    organization_id: str = Field(..., example="00000000-0000-0000-0000-000000000000")
    session_id: str = Field(..., example="task_test_001")
    message: str = Field(..., example="예약 시작")


@router.post("")
def create_task_flow(request: TaskFlowCreateRequest):
    client = get_supabase_client()

    payload = {
        "organization_id": request.organization_id,
        "name": request.name,
        "description": request.description,
        "trigger_intent": request.trigger_intent,
        "trigger_description": request.trigger_description,
        "trigger_examples": request.trigger_examples,
        "allowed_channels": request.allowed_channels,
        "filters": request.filters,
        "is_enabled": request.is_enabled,
    }

    response = (
        client.table("task_flows")
        .insert(payload)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=500,
            detail="Task flow creation failed.",
        )

    return rows[0]


@router.get("")
def list_task_flows(
    organization_id: str | None = Query(None),
    is_enabled: bool | None = Query(None),
):
    client = get_supabase_client()

    query = (
        client.table("task_flows")
        .select("*")
        .order("created_at", desc=True)
    )

    if organization_id:
        query = query.eq("organization_id", organization_id)

    if is_enabled is not None:
        query = query.eq("is_enabled", is_enabled)

    response = query.execute()

    return {
        "items": response.data or [],
        "count": len(response.data or []),
    }


@router.get("/{flow_id}")
def get_task_flow(flow_id: str):
    client = get_supabase_client()

    response = (
        client.table("task_flows")
        .select("*")
        .eq("id", flow_id)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Task flow not found.",
        )

    return rows[0]


@router.patch("/{flow_id}")
def update_task_flow(flow_id: str, request: TaskFlowUpdateRequest):
    client = get_supabase_client()

    payload = request.model_dump(exclude_unset=True)

    if not payload:
        raise HTTPException(
            status_code=400,
            detail="No fields to update.",
        )

    response = (
        client.table("task_flows")
        .update(payload)
        .eq("id", flow_id)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Task flow not found.",
        )

    return rows[0]


@router.delete("/{flow_id}")
def delete_task_flow(flow_id: str):
    client = get_supabase_client()

    existing_response = (
        client.table("task_flows")
        .select("id")
        .eq("id", flow_id)
        .limit(1)
        .execute()
    )

    existing_rows = existing_response.data or []

    if not existing_rows:
        raise HTTPException(
            status_code=404,
            detail="Task flow not found.",
        )

    (
        client.table("task_flows")
        .delete()
        .eq("id", flow_id)
        .execute()
    )

    return {
        "deleted": True,
        "flow_id": flow_id,
    }


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