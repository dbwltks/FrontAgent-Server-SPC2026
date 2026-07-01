from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.repositories.reservation_repo import (
    NotFoundError,
    ReservationConflictError,
    ReservationRepoError,
    cancel_reservation,
    confirm_reservation,
    create_reservation,
    create_service,
    delete_service,
    get_available_slots,
    get_reservation,
    get_service,
    list_reservations,
    list_services,
    reject_reservation,
    update_service,
)
from app.services.reservation_calendar_sync_service import (
    sync_cancelled_reservation_to_calendar,
    sync_confirmed_reservation_to_calendar,
)


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Reservations"])


class ReservationCreateRequest(BaseModel):
    organization_id: str = Field(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    )
    conversation_id: str | None = Field(
        default=None,
        examples=["00000000-0000-0000-0000-000000000000"],
    )
    service_id: str = Field(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    )

    service_item_id: str | None = Field(
        default=None,
        example="이사 청소 service_item_id",
    )

    selected_option_ids: list[str] = Field(
        default_factory=list,
        example=["24평형 option_id", "베란다 확장형 option_id"],
    )

    customer_name: str | None = Field(
        default=None,
        example="김민수",
    )
    customer_phone: str | None = Field(
        default=None,
        example="010-1234-5678",
    )
    customer_email: str | None = Field(
        default=None,
        example="customer@example.com",
    )

    start_at: datetime = Field(
        ...,
        example="2026-07-01T15:00:00+09:00",
    )
    end_at: datetime | None = Field(
        default=None,
        example="2026-07-01T16:30:00+09:00",
    )

    source_channel: str = Field(
        default="web_chat",
        example="web_chat",
    )
    memo: str | None = Field(
        default=None,
        example="방문 청소 희망",
    )

class ServiceCreateRequest(BaseModel):
    organization_id: str = Field(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    )
    name: str = Field(
        ...,
        example="프리미엄 청소",
    )
    description: str | None = Field(
        default=None,
        example="전문 장비를 사용하는 프리미엄 청소 서비스",
    )
    duration_minutes: int = Field(
        ...,
        gt=0,
        example=90,
    )
    is_active: bool = Field(
        default=True,
        example=True,
    )
    is_reservable: bool = Field(
        default=True,
        example=True,
    )


class ServiceUpdateRequest(BaseModel):
    name: str | None = Field(
        default=None,
        example="프리미엄 청소",
    )
    description: str | None = Field(
        default=None,
        example="전문 장비를 사용하는 프리미엄 청소 서비스",
    )
    duration_minutes: int | None = Field(
        default=None,
        gt=0,
        example=90,
    )
    is_active: bool | None = Field(
        default=None,
        example=True,
    )
    is_reservable: bool | None = Field(
        default=None,
        example=True,
    )


def _handle_repo_error(error: Exception) -> None:
    print("Reservation API ERROR:", type(error).__name__, str(error))

    if isinstance(error, NotFoundError):
        raise HTTPException(status_code=404, detail=str(error))

    if isinstance(error, ReservationConflictError):
        raise HTTPException(status_code=409, detail=str(error))

    if isinstance(error, ReservationRepoError):
        raise HTTPException(status_code=400, detail=str(error))

    raise HTTPException(status_code=500, detail="Reservation API error")


def _safe_sync_confirmed_reservation_to_calendar(
    organization_id: str,
    reservation_id: str,
) -> dict[str, Any]:
    """
    예약 확정 후 Google Calendar 동기화를 시도한다.

    Calendar sync 실패가 예약 확정 API 실패로 이어지면 안 된다.
    """
    try:
        return sync_confirmed_reservation_to_calendar(
            organization_id=organization_id,
            reservation_id=reservation_id,
            access_token=None,
        )
    except Exception as error:
        logger.warning(
            "Failed to sync confirmed reservation to calendar: reservation_id=%s",
            reservation_id,
            exc_info=True,
        )
        return {
            "ok": False,
            "status": "failed",
            "reservation_id": reservation_id,
            "error_message": str(error),
        }


def _safe_sync_cancelled_reservation_to_calendar(
    organization_id: str,
    reservation_id: str,
) -> dict[str, Any]:
    """
    예약 취소 후 Google Calendar 동기화를 시도한다.

    Calendar sync 실패가 예약 취소 API 실패로 이어지면 안 된다.
    """
    try:
        return sync_cancelled_reservation_to_calendar(
            organization_id=organization_id,
            reservation_id=reservation_id,
            access_token=None,
        )
    except Exception as error:
        logger.warning(
            "Failed to sync cancelled reservation to calendar: reservation_id=%s",
            reservation_id,
            exc_info=True,
        )
        return {
            "ok": False,
            "status": "failed",
            "reservation_id": reservation_id,
            "error_message": str(error),
        }


@router.get("/services")
def list_reservation_services_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약 가능한 서비스 목록을 조회한다.

    Task Function Node 연결 예:
    - reservation.list_services
    """
    try:
        items = list_services(organization_id)

        return {
            "organization_id": organization_id,
            "count": len(items),
            "items": items,
        }
    except Exception as error:
        _handle_repo_error(error)


@router.post("/services")
def create_service_api(
    request: ServiceCreateRequest,
) -> dict[str, Any]:
    """
    예약 서비스를 생성한다.

    관리자 화면에서 사장님이 새 예약 상품/서비스를 등록할 때 사용한다.
    """
    try:
        service = create_service(
            organization_id=request.organization_id,
            name=request.name,
            description=request.description,
            duration_minutes=request.duration_minutes,
            is_active=request.is_active,
            is_reservable=request.is_reservable,
        )

        return {
            "id": service["id"],
            "message": "서비스가 생성되었습니다.",
            "service": service,
        }

    except Exception as error:
        _handle_repo_error(error)

@router.patch("/services/{service_id}")
def update_service_api(
    service_id: str,
    request: ServiceUpdateRequest,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약 서비스를 수정한다.
    """
    update_data = request.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    try:
        service = update_service(
            organization_id=organization_id,
            service_id=service_id,
            update_data=update_data,
        )

        return {
            "id": service["id"],
            "message": "서비스가 수정되었습니다.",
            "service": service,
        }

    except Exception as error:
        _handle_repo_error(error)


@router.delete("/services/{service_id}")
def delete_service_api(
    service_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약 서비스를 삭제한다.

    실제 DB row를 삭제하지 않고,
    is_active=false, is_reservable=false 로 변경한다.
    """
    try:
        service = delete_service(
            organization_id=organization_id,
            service_id=service_id,
        )

        return {
            "id": service["id"],
            "deleted": True,
            "message": "서비스가 삭제되었습니다.",
            "service": service,
        }

    except Exception as error:
        _handle_repo_error(error)


@router.get("/services/{service_id}")
def get_service_api(
    service_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    서비스 상세 정보를 조회한다.
    """
    try:
        service = get_service(
            organization_id=organization_id,
            service_id=service_id,
        )

        if not service:
            raise HTTPException(status_code=404, detail="Service not found")

        return service
    except HTTPException:
        raise
    except Exception as error:
        _handle_repo_error(error)


@router.get("/booking/available-slots")
def get_available_slots_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    service_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    target_date: date = Query(
        ...,
        alias="date",
        examples=["2026-07-01"],
    ),
) -> dict[str, Any]:
    """
    특정 서비스의 예약 가능한 시간 목록을 조회한다.

    실제 URL 예:
    /booking/available-slots?organization_id=...&service_id=...&date=2026-07-01
    """
    try:
        return get_available_slots(
            organization_id=organization_id,
            service_id=service_id,
            target_date=target_date,
        )
    except Exception as error:
        _handle_repo_error(error)


@router.post("/reservations")
def create_reservation_api(
    request: ReservationCreateRequest,
) -> dict[str, Any]:
    """
    예약 요청을 생성한다.

    기본 상태:
    - requested

    확정은 관리자 승인 API에서 처리한다.
    """
    try:
        reservation = create_reservation(
            organization_id=request.organization_id,
            conversation_id=request.conversation_id,
            service_id=request.service_id,
            service_item_id=request.service_item_id,
            selected_option_ids=request.selected_option_ids,
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            customer_email=request.customer_email,
            start_at=request.start_at,
            end_at=request.end_at,
            source_channel=request.source_channel,
            memo=request.memo,
            created_by="ai",
        )

        return {
            "id": reservation["id"],
            "status": reservation["status"],
            "message": "예약 요청이 접수되었습니다.",
            "reservation": reservation,
        }
    except Exception as error:
        _handle_repo_error(error)


@router.get("/reservations")
def list_reservations_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    status: str | None = Query(default=None, examples=["requested"]),
    service_id: str | None = Query(default=None),
    date_from: datetime | None = Query(
        default=None,
        examples=["2026-07-01T00:00:00+09:00"],
    ),
    date_to: datetime | None = Query(
        default=None,
        examples=["2026-07-02T00:00:00+09:00"],
    ),
    customer_phone: str | None = Query(default=None, examples=["010-1234-5678"]),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """
    예약 목록을 조회한다.
    """
    try:
        items = list_reservations(
            organization_id=organization_id,
            status=status,
            service_id=service_id,
            date_from=date_from,
            date_to=date_to,
            customer_phone=customer_phone,
            limit=limit,
        )

        return {
            "organization_id": organization_id,
            "count": len(items),
            "items": items,
        }
    except Exception as error:
        _handle_repo_error(error)


@router.get("/reservations/{reservation_id}")
def get_reservation_api(
    reservation_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약 상세 정보를 조회한다.
    """
    try:
        reservation = get_reservation(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")

        return reservation
    except HTTPException:
        raise
    except Exception as error:
        _handle_repo_error(error)


@router.patch("/reservations/{reservation_id}/confirm")
def confirm_reservation_api(
    reservation_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약 요청을 확정한다.

    상태 변경:
    - requested -> confirmed

    Calendar sync:
    - confirmed 이후 Google Calendar 동기화를 시도한다.
    - OAuth가 아직 없으면 pending/skipped로 남는다.
    """
    try:
        reservation = confirm_reservation(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        calendar_sync = _safe_sync_confirmed_reservation_to_calendar(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        return {
            "id": reservation["id"],
            "status": reservation["status"],
            "message": "예약이 확정되었습니다.",
            "reservation": reservation,
            "calendar_sync": calendar_sync,
        }
    except Exception as error:
        _handle_repo_error(error)


@router.patch("/reservations/{reservation_id}/reject")
def reject_reservation_api(
    reservation_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약 요청을 거절한다.

    상태 변경:
    - requested -> rejected
    """
    try:
        reservation = reject_reservation(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        return {
            "id": reservation["id"],
            "status": reservation["status"],
            "message": "예약이 거절되었습니다.",
            "reservation": reservation,
        }
    except Exception as error:
        _handle_repo_error(error)


@router.patch("/reservations/{reservation_id}/cancel")
def cancel_reservation_api(
    reservation_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    예약을 취소한다.

    상태 변경:
    - requested/confirmed -> cancelled

    Calendar sync:
    - cancelled 이후 Google Calendar 이벤트 취소/삭제 동기화를 시도한다.
    - OAuth가 아직 없으면 pending/skipped로 남는다.
    """
    try:
        reservation = cancel_reservation(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        calendar_sync = _safe_sync_cancelled_reservation_to_calendar(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        return {
            "id": reservation["id"],
            "status": reservation["status"],
            "message": "예약이 취소되었습니다.",
            "reservation": reservation,
            "calendar_sync": calendar_sync,
        }
    except Exception as error:
        _handle_repo_error(error)