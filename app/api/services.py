from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.db import supabase
from app.services.service_sync_pipeline import extract_and_sync_services_from_knowledge
from app.repositories.service_repo import sync_service_items_and_options_from_payload

router = APIRouter(prefix="/services", tags=["Services"])


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

    hierarchy_sync_result = None

    source_payload = (
        service.get("raw_payload")
        or service.get("pending_payload")
        or approved_service.get("raw_payload")
        or approved_service.get("pending_payload")
    )

    if isinstance(source_payload, dict):
        hierarchy_sync_result = sync_service_items_and_options_from_payload(
            organization_id=req.organization_id,
            service_id=approved_service["id"],
            payload=source_payload,
        )

    return {
        "ok": True,
        "service": approved_service,
        "hierarchy_sync_result": hierarchy_sync_result,
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

    updated_service = rows[0]

    hierarchy_sync_result = None

    if isinstance(pending_payload, dict):
        hierarchy_sync_result = sync_service_items_and_options_from_payload(
            organization_id=req.organization_id,
            service_id=updated_service["id"],
            payload=pending_payload,
        )

    return {
        "ok": True,
        "service": updated_service,
        "hierarchy_sync_result": hierarchy_sync_result,
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