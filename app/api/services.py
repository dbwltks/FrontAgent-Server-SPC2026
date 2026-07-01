from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.db import supabase
from app.services.service_sync_pipeline import extract_and_sync_services_from_knowledge
from app.repositories.service_catalog_repo import activate_service_catalog

from app.repositories.service_repo import (
    list_active_services,
    get_service,
    list_service_items,
    get_service_item,
    list_service_item_options,
    calculate_service_price,
    update_service_item,
    deactivate_service_item,
    deactivate_service_item_options_by_item,
    get_service_item_option,
    create_service_item,
    create_service_item_option,
    update_service_item_option,
    deactivate_service_item_option,
    list_pending_service_items,
    list_pending_service_item_options,
)


router = APIRouter(prefix="/services", tags=["Services"])


class CalculateServicePriceRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="e255a5f0-ae6b-4364-892a-6f7cd1387988",
    )
    service_item_id: str = Field(
        ...,
        example="이사 청소 service_item_id",
    )
    option_ids: list[str] = Field(
        default_factory=list,
        example=["옵션 id 1", "옵션 id 2"],
    )

class CreateServiceItemRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
    )
    name: str = Field(..., example="이사 청소")
    description: str | None = Field(
        default=None,
        example="이사 전후 집 전체를 청소하는 서비스입니다.",
    )
    base_price: int | None = Field(default=None, example=150000)
    duration_minutes: int | None = Field(default=None, example=180)
    is_available: bool = Field(default=True, example=True)

class UpdateServiceItemRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="e255a5f0-ae6b-4364-892a-6f7cd1387988",
    )
    name: str | None = Field(default=None, example="화장실 청소")
    description: str | None = Field(default=None, example="화장실 전체 청소 서비스")
    base_price: int | None = Field(default=None, example=30000)
    duration_minutes: int | None = Field(default=None, example=60)
    is_available: bool | None = Field(default=None, example=True)


class CreateServiceItemOptionRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="e255a5f0-ae6b-4364-892a-6f7cd1387988",
    )
    option_group: str = Field(default="옵션", example="청소 범위")
    option_value: str = Field(..., example="곰팡이 제거 추가")
    description: str | None = Field(default=None, example="곰팡이 제거 작업을 추가합니다.")
    additional_price: int | None = Field(default=None, example=10000)
    additional_duration: int | None = Field(default=None, example=20)
    is_available: bool | None = Field(default=None, example=True)


class UpdateServiceItemOptionRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="e255a5f0-ae6b-4364-892a-6f7cd1387988",
    )
    option_group: str | None = Field(default=None, example="청소 범위")
    option_value: str | None = Field(default=None, example="곰팡이 제거 추가")
    description: str | None = Field(default=None, example="곰팡이 제거 작업을 추가합니다.")
    additional_price: int | None = Field(default=None, example=10000)
    additional_duration: int | None = Field(default=None, example=20)
    is_available: bool | None = Field(default=None, example=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExtractServicesFromKnowledgeRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    knowledge_source_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )


class ApproveServiceRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )

    # 관리자가 승인 전에 수정할 수 있는 값들
    name: str | None = None
    description: str | None = None
    price: int | None = None
    duration_minutes: int | None = None

    is_active: bool = True
    approved_by: str | None = None


class RejectServiceRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    reason: str | None = None


class ApplyPendingServiceRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )

    # pending_payload 값을 그대로 쓰지 않고 관리자가 수정해서 반영할 수 있음
    name: str | None = None
    description: str | None = None
    price: int | None = None
    duration_minutes: int | None = None

    approved_by: str | None = None

class IgnorePendingServiceRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    reason: str | None = None


class DeactivateServiceRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    reason: str | None = None



def _get_service_or_404(
    *,
    organization_id: str,
    service_id: str,
) -> dict[str, Any]:
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", service_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise HTTPException(status_code=404, detail="Service not found")

    return rows[0]

def _dump_exclude_unset(model: BaseModel) -> dict[str, Any]:
    """
    PATCH 요청에서 전달된 필드만 추출한다.

    중요:
    - 필드를 안 보내면 수정하지 않음
    - 필드를 null로 보내면 실제로 DB에 null 저장 가능
    """
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)

    return model.dict(exclude_unset=True)


def _remove_none_values(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}

REQUIRED_SERVICE_FIELDS = ["name", "price", "duration_minutes"]


def _get_missing_fields(service: dict[str, Any]) -> list[str]:
    """
    관리자 승인 전에 채워야 하는 필드 목록을 계산한다.

    name은 필수.
    price, duration_minutes는 업종에 따라 비워둘 수도 있지만,
    MVP 관리자 화면에서는 누락 정보로 보여주는 게 좋다.
    """
    missing_fields = []

    for field in REQUIRED_SERVICE_FIELDS:
        value = service.get(field)

        if value is None:
            missing_fields.append(field)
            continue

        if isinstance(value, str) and not value.strip():
            missing_fields.append(field)

    return missing_fields


def _build_current_service_payload(service: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": service.get("name"),
        "description": service.get("description"),
        "price": service.get("price"),
        "currency": service.get("currency"),
        "duration_minutes": service.get("duration_minutes"),
        "is_active": service.get("is_active"),
        "is_reservable": service.get("is_reservable"),
    }


def _build_ai_payload(service: dict[str, Any]) -> dict[str, Any]:
    raw_payload = service.get("raw_payload") or {}

    return {
        "confidence": service.get("confidence"),
        "reason": raw_payload.get("reason") if isinstance(raw_payload, dict) else None,
        "raw_payload": raw_payload,
    }


def _build_pending_service_admin_item(service: dict[str, Any]) -> dict[str, Any]:
    missing_fields = _get_missing_fields(service)

    return {
        "id": service.get("id"),
        "organization_id": service.get("organization_id"),
        "status": service.get("approval_status"),
        "sync_status": service.get("sync_status"),
        "source_type": service.get("source_type"),
        "source_id": service.get("source_id"),

        "name": service.get("name"),
        "can_approve": len(missing_fields) == 0,
        "missing_fields": missing_fields,

        "current": _build_current_service_payload(service),
        "ai": _build_ai_payload(service),

        "created_at": service.get("created_at"),
        "updated_at": service.get("updated_at"),
        "last_extracted_at": service.get("last_extracted_at"),
    }


def _get_changed_fields(
    *,
    current: dict[str, Any],
    pending_payload: dict[str, Any],
) -> list[str]:
    fields = ["name", "description", "price", "duration_minutes"]
    changed_fields = []

    for field in fields:
        if field not in pending_payload:
            continue

        if current.get(field) != pending_payload.get(field):
            changed_fields.append(field)

    return changed_fields


def _build_review_service_admin_item(service: dict[str, Any]) -> dict[str, Any]:
    pending_payload = service.get("pending_payload") or {}

    if not isinstance(pending_payload, dict):
        pending_payload = {}

    current = _build_current_service_payload(service)

    suggested = {
        "name": pending_payload.get("name"),
        "description": pending_payload.get("description"),
        "price": pending_payload.get("price"),
        "duration_minutes": pending_payload.get("duration_minutes"),
    }

    changed_fields = _get_changed_fields(
        current=current,
        pending_payload=suggested,
    )

    return {
        "id": service.get("id"),
        "organization_id": service.get("organization_id"),
        "status": service.get("approval_status"),
        "sync_status": service.get("sync_status"),
        "source_type": service.get("source_type"),
        "source_id": service.get("source_id"),

        "name": service.get("name"),
        "changed_fields": changed_fields,
        "has_changes": len(changed_fields) > 0,

        "current": current,
        "suggested": suggested,

        "ai": {
            "confidence": service.get("confidence"),
            "pending_payload": pending_payload,
        },

        "created_at": service.get("created_at"),
        "updated_at": service.get("updated_at"),
        "last_extracted_at": service.get("last_extracted_at"),
    }

def _build_stale_service_admin_item(service: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": service.get("id"),
        "organization_id": service.get("organization_id"),
        "status": service.get("approval_status"),
        "sync_status": service.get("sync_status"),
        "source_type": service.get("source_type"),
        "source_id": service.get("source_id"),

        "name": service.get("name"),
        "is_active": service.get("is_active"),
        "can_deactivate": bool(service.get("is_active")),

        "current": _build_current_service_payload(service),
        "ai": _build_ai_payload(service),

        "message": "이 서비스는 원본 지식에서 더 이상 발견되지 않습니다. 계속 유지하거나 비활성화할 수 있습니다.",

        "created_at": service.get("created_at"),
        "updated_at": service.get("updated_at"),
        "last_extracted_at": service.get("last_extracted_at"),
    }



@router.post("/extract-from-knowledge")
async def extract_services_from_knowledge(req: ExtractServicesFromKnowledgeRequest):
    """
    특정 knowledge_source_id에서 서비스 후보를 AI로 추출하고
    services 테이블에 pending 또는 needs_review 상태로 반영한다.
    """
    try:
        result = await extract_and_sync_services_from_knowledge(
            organization_id=req.organization_id,
            knowledge_source_id=req.knowledge_source_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to extract services from knowledge: {exc}",
        )

    return result


@router.get("/pending")
def list_pending_services(
    organization_id: str,
):
    """
    관리자 승인 대기 중인 서비스 후보 목록을 조회한다.

    프론트에서 바로 사용할 수 있도록
    누락 필드, 승인 가능 여부, AI 추출 근거를 함께 내려준다.
    """
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("approval_status", "pending")
        .order("created_at", desc=True)
        .execute()
    )

    rows = result.data or []
    items = [_build_pending_service_admin_item(row) for row in rows]

    return {
        "items": items,
        "count": len(items),
    }


@router.get("/review")
def list_services_needing_review(
    organization_id: str,
):
    """
    이미 approved 상태인 서비스 중,
    지식 수정/재추출로 인해 변경 후보가 생긴 서비스 목록을 조회한다.

    프론트에서 현재 값과 AI 제안 값을 비교해서 보여줄 수 있도록
    changed_fields를 함께 내려준다.
    """
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("sync_status", "needs_review")
        .order("last_extracted_at", desc=True)
        .execute()
    )

    rows = result.data or []
    items = [_build_review_service_admin_item(row) for row in rows]

    return {
        "items": items,
        "count": len(items),
    }


@router.get("/stale")
def list_stale_services(
    organization_id: str,
):
    """
    원본 지식에서 더 이상 발견되지 않는 서비스 목록을 조회한다.

    자동 삭제하지 않고 관리자에게 유지/비활성화 선택권을 준다.
    """
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("sync_status", "stale")
        .order("last_extracted_at", desc=True)
        .execute()
    )

    rows = result.data or []
    items = [_build_stale_service_admin_item(row) for row in rows]

    return {
        "items": items,
        "count": len(items),
    }

@router.post("/{service_id}/approve")
def approve_service(
    service_id: str,
    req: ApproveServiceRequest,
):
    """
    pending 상태의 서비스를 승인한다.

    관리자는 승인할 때 name, description, price, duration_minutes를 수정할 수 있다.
    승인되면 예약 선택지에 노출된다.
    """
    service = _get_service_or_404(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    if service.get("approval_status") == "approved":
        return {
            "ok": True,
            "message": "이미 승인된 서비스입니다.",
            "service": service,
        }

    update_payload = _remove_none_values(
        {
            "name": req.name,
            "description": req.description,
            "price": req.price,
            "duration_minutes": req.duration_minutes,
        }
    )

    update_payload.update(
        {
            "approval_status": "approved",
            "is_active": req.is_active,
            "sync_status": "synced",
            "pending_payload": None,
            "approved_at": _now_iso(),
            "approved_by": req.approved_by,
        }
    )

    result = (
        supabase.table("services")
        .update(update_payload)
        .eq("organization_id", req.organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise HTTPException(status_code=500, detail="Failed to approve service")

    approved_service = rows[0]

    catalog_activation = activate_service_catalog(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    return {
        "ok": True,
        "service": approved_service,
        "catalog_activation": catalog_activation,
    }


@router.post("/{service_id}/reject")
def reject_service(
    service_id: str,
    req: RejectServiceRequest,
):
    """
    AI가 잘못 추출한 서비스를 거절한다.
    거절된 서비스는 예약 선택지에 노출되지 않는다.
    """
    _get_service_or_404(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    result = (
        supabase.table("services")
        .update(
            {
                "approval_status": "rejected",
                "is_active": False,
                "sync_status": "synced",
                "rejected_reason": req.reason,
            }
        )
        .eq("organization_id", req.organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise HTTPException(status_code=500, detail="Failed to reject service")

    return {
        "ok": True,
        "service": rows[0],
    }


@router.post("/{service_id}/apply-pending")
def apply_pending_service_change(
    service_id: str,
    req: ApplyPendingServiceRequest,
):
    """
    approved 서비스에 대해 지식 수정으로 생긴 pending_payload를 실제 서비스 값에 반영한다.

    예:
    현재 서비스:
      price = 30000

    지식 재추출 후보:
      pending_payload.price = 35000

    관리자가 승인하면:
      price = 35000
      pending_payload = null
      sync_status = synced
    """
    service = _get_service_or_404(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    pending_payload = service.get("pending_payload")

    if not pending_payload:
        raise HTTPException(
            status_code=400,
            detail="No pending payload to apply",
        )

    update_payload = {
        "name": req.name if req.name is not None else pending_payload.get("name"),
        "description": (
            req.description
            if req.description is not None
            else pending_payload.get("description")
        ),
        "price": req.price if req.price is not None else pending_payload.get("price"),
        "duration_minutes": (
            req.duration_minutes
            if req.duration_minutes is not None
            else pending_payload.get("duration_minutes")
        ),
        "raw_payload": pending_payload,
        "pending_payload": None,
        "sync_status": "synced",
        "approval_status": "approved",
        "is_active": True,
        "approved_at": _now_iso(),
        "approved_by": req.approved_by,
    }

    # name이 null이면 기존 이름 유지
    update_payload = {
        key: value
        for key, value in update_payload.items()
        if value is not None
    }

    result = (
        supabase.table("services")
        .update(update_payload)
        .eq("organization_id", req.organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise HTTPException(
            status_code=500,
            detail="Failed to apply pending service change",
        )

    applied_service = rows[0]

    catalog_activation = activate_service_catalog(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    return {
        "ok": True,
        "service": applied_service,
        "catalog_activation": catalog_activation,
    }

@router.post("/{service_id}/ignore-pending")
def ignore_pending_service_change(
    service_id: str,
    req: IgnorePendingServiceRequest,
):
    """
    approved 서비스에 생긴 변경 후보를 무시한다.

    예:
    현재 서비스:
      price = 20000

    지식 재추출 후보:
      pending_payload.price = 25000

    관리자가 무시하면:
      price = 20000 유지
      pending_payload = null
      sync_status = synced
    """
    service = _get_service_or_404(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    pending_payload = service.get("pending_payload")
    sync_status = service.get("sync_status")

    if not pending_payload and sync_status != "needs_review":
        raise HTTPException(
            status_code=400,
            detail="No pending change to ignore",
        )

    result = (
        supabase.table("services")
        .update(
            {
                "pending_payload": None,
                "sync_status": "synced",
            }
        )
        .eq("organization_id", req.organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise HTTPException(
            status_code=500,
            detail="Failed to ignore pending service change",
        )

    return {
        "ok": True,
        "message": "변경 후보를 무시했습니다. 기존 서비스 값은 유지됩니다.",
        "reason": req.reason,
        "service": rows[0],
    }

@router.post("/{service_id}/deactivate")
def deactivate_service(
    service_id: str,
    req: DeactivateServiceRequest,
):
    """
    서비스를 비활성화한다.

    비활성화된 서비스는 예약 선택지에 노출되지 않는다.
    approval_status는 유지하고 is_active만 false로 바꾼다.
    """
    service = _get_service_or_404(
        organization_id=req.organization_id,
        service_id=service_id,
    )

    if service.get("approval_status") == "pending":
        raise HTTPException(
            status_code=400,
            detail="Pending service should be rejected instead of deactivated",
        )

    result = (
        supabase.table("services")
        .update(
            {
                "is_active": False,
                "sync_status": "synced",
                "pending_payload": None,
            }
        )
        .eq("organization_id", req.organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise HTTPException(
            status_code=500,
            detail="Failed to deactivate service",
        )

    return {
        "ok": True,
        "message": "서비스를 비활성화했습니다. 이제 예약 선택지에 노출되지 않습니다.",
        "reason": req.reason,
        "service": rows[0],
    }


@router.get("")
def list_services_api(
    organization_id: str = Query(..., example="e255a5f0-ae6b-4364-892a-6f7cd1387988"),
):
    services = list_active_services(organization_id=organization_id)

    return {
        "organization_id": organization_id,
        "count": len(services),
        "items": services,
    }

@router.post("/{service_id}/items")
def create_service_item_api(
    service_id: str,
    request: CreateServiceItemRequest,
):
    """
    특정 서비스 대분류 아래에 실제 예약 가능한 상품을 추가한다.

    예:
    - 청소 서비스 아래에 이사 청소 추가
    - 청소 서비스 아래에 화장실 청소 추가
    - 미용 서비스 아래에 커트 추가
    """
    service = get_service(
        organization_id=request.organization_id,
        service_id=service_id,
    )

    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    name = str(request.name or "").strip()
    if not name:
        raise HTTPException(
            status_code=400,
            detail="name is required",
        )

    if request.base_price is not None and request.base_price < 0:
        raise HTTPException(
            status_code=400,
            detail="base_price cannot be negative",
        )

    if request.duration_minutes is not None and request.duration_minutes < 0:
        raise HTTPException(
            status_code=400,
            detail="duration_minutes cannot be negative",
        )

    item = create_service_item(
        organization_id=request.organization_id,
        service_id=service_id,
        item={
            "name": name,
            "description": request.description,
            "base_price": request.base_price,
            "duration_minutes": request.duration_minutes,
            "is_available": request.is_available,
        },
    )

    return {
        "ok": True,
        "message": "서비스 아이템을 추가했습니다.",
        "service": {
            "id": service.get("id"),
            "name": service.get("name"),
        },
        "item": item,
    }

@router.get("/{service_id}/items")
def list_service_items_by_service_api(
    service_id: str,
    organization_id: str = Query(..., example="e255a5f0-ae6b-4364-892a-6f7cd1387988"),
):
    service = get_service(
        organization_id=organization_id,
        service_id=service_id,
    )

    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    items = list_service_items(
        organization_id=organization_id,
        service_id=service_id,
    )

    return {
        "organization_id": organization_id,
        "service_id": service_id,
        "service_name": service.get("name"),
        "count": len(items),
        "items": items,
    }


@router.get("/items")
def list_service_items_api(
    organization_id: str = Query(..., example="e255a5f0-ae6b-4364-892a-6f7cd1387988"),
    service_id: str | None = Query(default=None),
):
    items = list_service_items(
        organization_id=organization_id,
        service_id=service_id,
    )

    return {
        "organization_id": organization_id,
        "service_id": service_id,
        "count": len(items),
        "items": items,
    }

@router.get("/items/pending")
def list_pending_service_items_api(
    organization_id: str = Query(
        ...,
        example="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
    ),
    service_id: str | None = Query(
        default=None,
        description="특정 대분류 서비스 아래의 아이템만 조회할 때 사용",
    ),
    include_options: bool = Query(
        default=True,
        description="각 서비스 아이템 아래 옵션 목록도 함께 포함할지 여부",
    ),
    include_needs_review: bool = Query(
        default=True,
        description="pending뿐 아니라 sync_status=needs_review 항목도 포함할지 여부",
    ),
):
    """
    검토가 필요한 서비스 아이템 목록 조회.

    사용 예:
    - AI가 새 파일에서 추출한 신규 서비스 아이템 확인
    - 가격/시간이 null인 서비스 확인
    - 기존 승인값과 새 파일 값이 달라서 needs_review 된 항목 확인
    """
    items = list_pending_service_items(
        organization_id=organization_id,
        service_id=service_id,
        include_options=include_options,
        include_needs_review=include_needs_review,
    )

    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.get("/items/{service_item_id}")
def get_service_item_api(
    service_item_id: str,
    organization_id: str = Query(..., example="e255a5f0-ae6b-4364-892a-6f7cd1387988"),
):
    item = get_service_item(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    if not item:
        raise HTTPException(status_code=404, detail="Service item not found")

    return item


@router.patch("/items/{service_item_id}")
def update_service_item_api(
    service_item_id: str,
    request: UpdateServiceItemRequest,
):
    """
    서비스 아이템 수정 API.

    사용 예:
    - AI가 추출한 가격이 null이면 관리자가 가격 입력
    - 소요시간 수정
    - 상품명/설명 수정
    - 예약 선택지 노출 여부 수정
    """
    item = get_service_item(
        organization_id=request.organization_id,
        service_item_id=service_item_id,
    )

    if not item:
        raise HTTPException(status_code=404, detail="Service item not found")

    update_payload = _dump_exclude_unset(request)
    update_payload.pop("organization_id", None)

    if "name" in update_payload:
        name = update_payload.get("name")
        if name is None or not str(name).strip():
            raise HTTPException(
                status_code=400,
                detail="name cannot be empty",
            )
        update_payload["name"] = str(name).strip()

    if "base_price" in update_payload:
        base_price = update_payload.get("base_price")
        if base_price is not None and base_price < 0:
            raise HTTPException(
                status_code=400,
                detail="base_price cannot be negative",
            )

    if "duration_minutes" in update_payload:
        duration_minutes = update_payload.get("duration_minutes")
        if duration_minutes is not None and duration_minutes < 0:
            raise HTTPException(
                status_code=400,
                detail="duration_minutes cannot be negative",
            )

    updated = update_service_item(
        organization_id=request.organization_id,
        service_item_id=service_item_id,
        updates=update_payload,
    )

    if not updated:
        raise HTTPException(
            status_code=500,
            detail="Failed to update service item",
        )

    return {
        "ok": True,
        "message": "서비스 아이템을 수정했습니다.",
        "item": updated,
    }


@router.delete("/items/{service_item_id}")
def delete_service_item_api(
    service_item_id: str,
    organization_id: str = Query(
        ...,
        example="e255a5f0-ae6b-4364-892a-6f7cd1387988",
    ),
):
    """
    서비스 아이템 삭제 API.

    실제 삭제가 아니라 is_available=false 처리한다.
    이미 생성된 예약 내역과의 연결이 깨지지 않게 하기 위함.
    """
    item = get_service_item(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    if not item:
        raise HTTPException(status_code=404, detail="Service item not found")

    deactivated_item = deactivate_service_item(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    deactivated_options = deactivate_service_item_options_by_item(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    if not deactivated_item:
        raise HTTPException(
            status_code=500,
            detail="Failed to deactivate service item",
        )

    return {
        "ok": True,
        "message": "서비스 아이템을 비활성화했습니다. 예약 선택지에는 더 이상 노출되지 않습니다.",
        "item": deactivated_item,
        "deactivated_options_count": len(deactivated_options),
        "deactivated_options": deactivated_options,
    }

@router.get("/options/pending")
def list_pending_service_item_options_api(
    organization_id: str = Query(
        ...,
        example="a55c98f9-74ba-40d8-bc9d-bc3f1c0870da",
    ),
    service_item_id: str | None = Query(
        default=None,
        description="특정 서비스 아이템의 옵션만 조회할 때 사용",
    ),
    include_needs_review: bool = Query(
        default=True,
        description="pending뿐 아니라 sync_status=needs_review 항목도 포함할지 여부",
    ),
):
    """
    검토가 필요한 서비스 옵션 목록 조회.

    사용 예:
    - 신규 옵션 검토
    - 옵션 가격/시간이 null인 항목 확인
    - 기존 승인 옵션과 새 파일 값이 달라서 needs_review 된 항목 확인
    """
    options = list_pending_service_item_options(
        organization_id=organization_id,
        service_item_id=service_item_id,
        include_needs_review=include_needs_review,
    )

    return {
        "ok": True,
        "count": len(options),
        "options": options,
    }


@router.post("/items/{service_item_id}/options")
def create_service_item_option_api(
    service_item_id: str,
    request: CreateServiceItemOptionRequest,
):
    """
    서비스 아이템 옵션 추가 API.

    사용 예:
    - 평수 옵션 추가
    - 곰팡이 제거 추가
    - 창틀 청소 추가
    - 방문 거리 추가금 옵션 추가
    """
    item = get_service_item(
        organization_id=request.organization_id,
        service_item_id=service_item_id,
    )

    if not item:
        raise HTTPException(status_code=404, detail="Service item not found")

    option_value = str(request.option_value or "").strip()
    if not option_value:
        raise HTTPException(
            status_code=400,
            detail="option_value is required",
        )

    if request.additional_price is not None and request.additional_price < 0:
        raise HTTPException(
            status_code=400,
            detail="additional_price cannot be negative",
        )

    if request.additional_duration is not None and request.additional_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="additional_duration cannot be negative",
        )

    is_available = (
        request.is_available
        if request.is_available is not None
        else bool(item.get("is_available"))
    )

    option = create_service_item_option(
        organization_id=request.organization_id,
        service_item_id=service_item_id,
        option={
            "option_group": request.option_group,
            "option_value": option_value,
            "description": request.description,
            "additional_price": request.additional_price,
            "additional_duration": request.additional_duration,
            "is_available": is_available,
        },
    )

    return {
        "ok": True,
        "message": "서비스 옵션을 추가했습니다.",
        "option": option,
    }


@router.patch("/options/{option_id}")
def update_service_item_option_api(
    option_id: str,
    request: UpdateServiceItemOptionRequest,
):
    """
    서비스 옵션 수정 API.

    사용 예:
    - 옵션명 수정
    - 추가금 수정
    - 추가 소요시간 수정
    - 옵션 노출 여부 수정
    """
    option = get_service_item_option(
        organization_id=request.organization_id,
        option_id=option_id,
    )

    if not option:
        raise HTTPException(status_code=404, detail="Service item option not found")

    update_payload = _dump_exclude_unset(request)
    update_payload.pop("organization_id", None)

    if "option_value" in update_payload:
        option_value = update_payload.get("option_value")
        if option_value is None or not str(option_value).strip():
            raise HTTPException(
                status_code=400,
                detail="option_value cannot be empty",
            )
        update_payload["option_value"] = str(option_value).strip()

    if "option_group" in update_payload:
        option_group = update_payload.get("option_group")
        if option_group is None or not str(option_group).strip():
            update_payload["option_group"] = "옵션"
        else:
            update_payload["option_group"] = str(option_group).strip()

    if "additional_price" in update_payload:
        additional_price = update_payload.get("additional_price")
        if additional_price is not None and additional_price < 0:
            raise HTTPException(
                status_code=400,
                detail="additional_price cannot be negative",
            )

    if "additional_duration" in update_payload:
        additional_duration = update_payload.get("additional_duration")
        if additional_duration is not None and additional_duration < 0:
            raise HTTPException(
                status_code=400,
                detail="additional_duration cannot be negative",
            )

    updated = update_service_item_option(
        organization_id=request.organization_id,
        option_id=option_id,
        updates=update_payload,
    )

    if not updated:
        raise HTTPException(
            status_code=500,
            detail="Failed to update service item option",
        )

    return {
        "ok": True,
        "message": "서비스 옵션을 수정했습니다.",
        "option": updated,
    }


@router.delete("/options/{option_id}")
def delete_service_item_option_api(
    option_id: str,
    organization_id: str = Query(
        ...,
        example="e255a5f0-ae6b-4364-892a-6f7cd1387988",
    ),
):
    """
    서비스 옵션 삭제 API.

    실제 삭제가 아니라 is_available=false 처리한다.
    """
    option = get_service_item_option(
        organization_id=organization_id,
        option_id=option_id,
    )

    if not option:
        raise HTTPException(status_code=404, detail="Service item option not found")

    deactivated = deactivate_service_item_option(
        organization_id=organization_id,
        option_id=option_id,
    )

    if not deactivated:
        raise HTTPException(
            status_code=500,
            detail="Failed to deactivate service item option",
        )

    return {
        "ok": True,
        "message": "서비스 옵션을 비활성화했습니다. 예약 선택지에는 더 이상 노출되지 않습니다.",
        "option": deactivated,
    }




@router.get("/items/{service_item_id}/options")
def list_service_item_options_api(
    service_item_id: str,
    organization_id: str = Query(..., example="e255a5f0-ae6b-4364-892a-6f7cd1387988"),
):
    item = get_service_item(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    if not item:
        raise HTTPException(status_code=404, detail="Service item not found")

    options = list_service_item_options(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    return {
        "organization_id": organization_id,
        "service_item_id": service_item_id,
        "service_item_name": item.get("name"),
        "count": len(options),
        "items": options,
    }

@router.post("/items/calculate-price")
def calculate_service_price_api(
    request: CalculateServicePriceRequest,
):
    """
    서비스 아이템과 선택 옵션을 기준으로 최종 가격과 소요 시간을 계산한다.
    """
    try:
        result = calculate_service_price(
            organization_id=request.organization_id,
            service_item_id=request.service_item_id,
            option_ids=request.option_ids,
        )

        return result

    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        )