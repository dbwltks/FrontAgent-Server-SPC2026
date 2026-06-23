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

def ensure_task_flow_exists(client: Client, flow_id: str) -> None:
    response = (
        client.table("task_flows")
        .select("id")
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


def ensure_task_node_exists(
    client: Client,
    flow_id: str,
    node_id: str,
) -> dict[str, Any]:
    response = (
        client.table("task_nodes")
        .select("*")
        .eq("id", node_id)
        .eq("flow_id", flow_id)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Task node not found.",
        )

    return rows[0]

def ensure_task_node_key_exists(
    client: Client,
    flow_id: str,
    node_key: str,
) -> None:
    response = (
        client.table("task_nodes")
        .select("id")
        .eq("flow_id", flow_id)
        .eq("node_key", node_key)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Task node key not found: {node_key}",
        )


def ensure_task_edge_exists(
    client: Client,
    flow_id: str,
    edge_id: str,
) -> dict[str, Any]:
    response = (
        client.table("task_edges")
        .select("*")
        .eq("id", edge_id)
        .eq("flow_id", flow_id)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Task edge not found.",
        )

    return rows[0]



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


class TaskNodeCreateRequest(BaseModel):
    node_key: str = Field(..., example="ask_date")
    node_type: str = Field(..., example="ask")
    label: str = Field(..., example="예약 날짜 질문")

    config: dict[str, Any] = Field(default_factory=dict)
    code: str | None = None

    position_x: int = 0
    position_y: int = 0

    timeout_seconds: int = 10
    retry_limit: int = 0


class TaskNodeUpdateRequest(BaseModel):
    node_key: str | None = None
    node_type: str | None = None
    label: str | None = None

    config: dict[str, Any] | None = None
    code: str | None = None

    position_x: int | None = None
    position_y: int | None = None

    timeout_seconds: int | None = None
    retry_limit: int | None = None


class TaskEdgeCreateRequest(BaseModel):
    source_node_key: str = Field(..., example="ask_date")
    target_node_key: str = Field(..., example="ask_time")

    edge_type: str = Field(default="single", example="single")
    condition_type: str = Field(default="always", example="always")
    condition_config: dict[str, Any] = Field(default_factory=dict)

    is_failure_edge: bool = False
    priority: int = 100


class TaskEdgeUpdateRequest(BaseModel):
    source_node_key: str | None = None
    target_node_key: str | None = None

    edge_type: str | None = None
    condition_type: str | None = None
    condition_config: dict[str, Any] | None = None

    is_failure_edge: bool | None = None
    priority: int | None = None



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

@router.post("/{flow_id}/nodes")
def create_task_node(flow_id: str, request: TaskNodeCreateRequest):
    client = get_supabase_client()

    ensure_task_flow_exists(client, flow_id)

    payload = {
        "flow_id": flow_id,
        "node_key": request.node_key,
        "node_type": request.node_type,
        "label": request.label,
        "config": request.config,
        "code": request.code,
        "position_x": request.position_x,
        "position_y": request.position_y,
        "timeout_seconds": request.timeout_seconds,
        "retry_limit": request.retry_limit,
    }

    response = (
        client.table("task_nodes")
        .insert(payload)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=500,
            detail="Task node creation failed.",
        )

    return rows[0]


@router.get("/{flow_id}/nodes")
def list_task_nodes(flow_id: str):
    client = get_supabase_client()

    ensure_task_flow_exists(client, flow_id)

    response = (
        client.table("task_nodes")
        .select("*")
        .eq("flow_id", flow_id)
        .order("created_at")
        .execute()
    )

    return {
        "items": response.data or [],
        "count": len(response.data or []),
    }


@router.get("/{flow_id}/nodes/{node_id}")
def get_task_node(flow_id: str, node_id: str):
    client = get_supabase_client()

    node = ensure_task_node_exists(
        client=client,
        flow_id=flow_id,
        node_id=node_id,
    )

    return node


@router.patch("/{flow_id}/nodes/{node_id}")
def update_task_node(
    flow_id: str,
    node_id: str,
    request: TaskNodeUpdateRequest,
):
    client = get_supabase_client()

    ensure_task_node_exists(
        client=client,
        flow_id=flow_id,
        node_id=node_id,
    )

    payload = request.model_dump(exclude_unset=True)

    if not payload:
        raise HTTPException(
            status_code=400,
            detail="No fields to update.",
        )

    response = (
        client.table("task_nodes")
        .update(payload)
        .eq("id", node_id)
        .eq("flow_id", flow_id)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Task node not found.",
        )

    return rows[0]


@router.delete("/{flow_id}/nodes/{node_id}")
def delete_task_node(flow_id: str, node_id: str):
    client = get_supabase_client()

    ensure_task_node_exists(
        client=client,
        flow_id=flow_id,
        node_id=node_id,
    )

    (
        client.table("task_nodes")
        .delete()
        .eq("id", node_id)
        .eq("flow_id", flow_id)
        .execute()
    )

    return {
        "deleted": True,
        "flow_id": flow_id,
        "node_id": node_id,
    }


@router.post("/{flow_id}/edges")
def create_task_edge(flow_id: str, request: TaskEdgeCreateRequest):
    client = get_supabase_client()

    ensure_task_flow_exists(client, flow_id)
    ensure_task_node_key_exists(client, flow_id, request.source_node_key)
    ensure_task_node_key_exists(client, flow_id, request.target_node_key)

    payload = {
        "flow_id": flow_id,
        "source_node_key": request.source_node_key,
        "target_node_key": request.target_node_key,
        "edge_type": request.edge_type,
        "condition_type": request.condition_type,
        "condition_config": request.condition_config,
        "is_failure_edge": request.is_failure_edge,
        "priority": request.priority,
    }

    response = (
        client.table("task_edges")
        .insert(payload)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=500,
            detail="Task edge creation failed.",
        )

    return rows[0]


@router.get("/{flow_id}/edges")
def list_task_edges(flow_id: str):
    client = get_supabase_client()

    ensure_task_flow_exists(client, flow_id)

    response = (
        client.table("task_edges")
        .select("*")
        .eq("flow_id", flow_id)
        .order("priority")
        .execute()
    )

    return {
        "items": response.data or [],
        "count": len(response.data or []),
    }


@router.get("/{flow_id}/edges/{edge_id}")
def get_task_edge(flow_id: str, edge_id: str):
    client = get_supabase_client()

    edge = ensure_task_edge_exists(
        client=client,
        flow_id=flow_id,
        edge_id=edge_id,
    )

    return edge


@router.patch("/{flow_id}/edges/{edge_id}")
def update_task_edge(
    flow_id: str,
    edge_id: str,
    request: TaskEdgeUpdateRequest,
):
    client = get_supabase_client()

    ensure_task_edge_exists(
        client=client,
        flow_id=flow_id,
        edge_id=edge_id,
    )

    payload = request.model_dump(exclude_unset=True)

    if not payload:
        raise HTTPException(
            status_code=400,
            detail="No fields to update.",
        )

    if "source_node_key" in payload:
        ensure_task_node_key_exists(
            client=client,
            flow_id=flow_id,
            node_key=payload["source_node_key"],
        )

    if "target_node_key" in payload:
        ensure_task_node_key_exists(
            client=client,
            flow_id=flow_id,
            node_key=payload["target_node_key"],
        )

    response = (
        client.table("task_edges")
        .update(payload)
        .eq("id", edge_id)
        .eq("flow_id", flow_id)
        .execute()
    )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Task edge not found.",
        )

    return rows[0]


@router.delete("/{flow_id}/edges/{edge_id}")
def delete_task_edge(flow_id: str, edge_id: str):
    client = get_supabase_client()

    ensure_task_edge_exists(
        client=client,
        flow_id=flow_id,
        edge_id=edge_id,
    )

    (
        client.table("task_edges")
        .delete()
        .eq("id", edge_id)
        .eq("flow_id", flow_id)
        .execute()
    )

    return {
        "deleted": True,
        "flow_id": flow_id,
        "edge_id": edge_id,
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