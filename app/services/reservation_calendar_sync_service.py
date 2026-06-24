from __future__ import annotations

from typing import Any
from app.repositories.calendar_integration_repo import get_google_access_token


from app.repositories.reservation_calendar_event_repo import (
    get_calendar_event_mapping_by_reservation,
    mark_calendar_event_cancelled,
    mark_calendar_event_failed,
    mark_calendar_event_pending,
    mark_calendar_event_synced,
)
from app.repositories.reservation_repo import (
    get_booking_calendar,
    get_reservation,
    get_service,
)
from app.services.google_calendar_service import (
    GoogleCalendarServiceError,
    create_google_calendar_event,
    delete_google_calendar_event,
)


def _is_google_calendar(calendar: dict[str, Any] | None) -> bool:
    if not calendar:
        return False

    provider = calendar.get("external_provider") or calendar.get("calendar_type")

    return provider == "google"


def _resolve_google_access_token(
    organization_id: str,
    external_calendar_id: str,
    access_token: str | None = None,
) -> str | None:
    """
    Google Calendar access_token을 결정한다.

    우선순위:
    1. 함수 인자로 직접 받은 access_token
    2. calendar_integrations 테이블에 저장된 access_token

    아직 OAuth 연결이 안 되어 있거나 status가 connected가 아니면 None을 반환한다.
    """

    if access_token:
        return access_token

    return get_google_access_token(
        organization_id=organization_id,
        external_calendar_id=external_calendar_id,
    )




def sync_confirmed_reservation_to_calendar(
    organization_id: str,
    reservation_id: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """
    confirmed 상태의 예약을 Google Calendar에 동기화한다.

    현재 단계에서는 OAuth 토큰 저장 구조가 없으므로 access_token은 선택값이다.
    access_token이 없으면 실제 Google API 호출은 하지 않고 pending으로 남긴다.
    """

    reservation = get_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
    )

    if reservation.get("status") != "confirmed":
        return {
            "ok": True,
            "status": "skipped",
            "reason": f"reservation_status_is_{reservation.get('status')}",
            "reservation_id": reservation_id,
        }

    calendar = get_booking_calendar(
        organization_id=organization_id,
        calendar_id=reservation.get("calendar_id"),
    )

    if not _is_google_calendar(calendar):
        return {
            "ok": True,
            "status": "skipped",
            "reason": "booking_calendar_is_not_google",
            "reservation_id": reservation_id,
        }

    external_calendar_id = calendar.get("external_calendar_id")

    if not external_calendar_id:
        mapping = mark_calendar_event_pending(
            organization_id=organization_id,
            reservation_id=reservation_id,
            calendar_id=calendar.get("id"),
            provider="google",
            error_message="Google external_calendar_id is not configured.",
        )

        return {
            "ok": True,
            "status": "pending",
            "reason": "external_calendar_id_missing",
            "reservation_id": reservation_id,
            "mapping": mapping,
        }

    resolved_access_token = _resolve_google_access_token(
        organization_id=organization_id,
        external_calendar_id=external_calendar_id,
        access_token=access_token,
    )

    if not resolved_access_token:
        mapping = mark_calendar_event_pending(
            organization_id=organization_id,
            reservation_id=reservation_id,
            calendar_id=calendar.get("id"),
            provider="google",
            error_message="Google OAuth access_token is not configured or not connected.",
        )

        return {
            "ok": True,
            "status": "pending",
            "reason": "access_token_missing",
            "reservation_id": reservation_id,
            "mapping": mapping,
        }

    try:
        service = None

        if reservation.get("service_id"):
            service = get_service(
                organization_id=organization_id,
                service_id=reservation["service_id"],
            )

        google_event = create_google_calendar_event(
            access_token=resolved_access_token,
            calendar_id=external_calendar_id,
            reservation=reservation,
            service=service,
        )

        mapping = mark_calendar_event_synced(
            organization_id=organization_id,
            reservation_id=reservation_id,
            external_event_id=google_event.get("id"),
            external_event_url=google_event.get("htmlLink"),
            provider="google",
        )

        return {
            "ok": True,
            "status": "synced",
            "reservation_id": reservation_id,
            "external_event_id": google_event.get("id"),
            "external_event_url": google_event.get("htmlLink"),
            "mapping": mapping,
        }

    except Exception as error:
        mapping = mark_calendar_event_failed(
            organization_id=organization_id,
            reservation_id=reservation_id,
            calendar_id=calendar.get("id"),
            provider="google",
            error_message=str(error),
        )

        return {
            "ok": False,
            "status": "failed",
            "reservation_id": reservation_id,
            "error_message": str(error),
            "mapping": mapping,
        }


def sync_cancelled_reservation_to_calendar(
    organization_id: str,
    reservation_id: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """
    cancelled 상태의 예약과 연결된 Google Calendar 이벤트를 삭제한다.

    현재 단계에서는 OAuth 토큰 저장 구조가 없으므로 access_token은 선택값이다.
    access_token이 없으면 실제 Google API 호출은 하지 않고 상태만 반환한다.
    """

    mapping = get_calendar_event_mapping_by_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider="google",
    )

    if not mapping:
        return {
            "ok": True,
            "status": "skipped",
            "reason": "calendar_event_mapping_not_found",
            "reservation_id": reservation_id,
        }

    external_event_id = mapping.get("external_event_id")

    if not external_event_id:
        cancelled_mapping = mark_calendar_event_cancelled(
            organization_id=organization_id,
            reservation_id=reservation_id,
            provider="google",
        )

        return {
            "ok": True,
            "status": "cancelled",
            "reason": "external_event_id_missing",
            "reservation_id": reservation_id,
            "mapping": cancelled_mapping,
        }

    reservation = get_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
    )

    calendar = None

    if reservation and reservation.get("calendar_id"):
        calendar = get_booking_calendar(
            organization_id=organization_id,
            calendar_id=reservation["calendar_id"],
        )

    external_calendar_id = calendar.get("external_calendar_id") if calendar else None

    if not external_calendar_id:
        return {
            "ok": True,
            "status": "skipped",
            "reason": "external_calendar_id_missing",
            "reservation_id": reservation_id,
            "mapping": mapping,
        }

    resolved_access_token = _resolve_google_access_token(
        organization_id=organization_id,
        external_calendar_id=external_calendar_id,
        access_token=access_token,
    )

    if not resolved_access_token:
        return {
            "ok": True,
            "status": "pending",
            "reason": "access_token_missing",
            "reservation_id": reservation_id,
            "mapping": mapping,
        }

    try:
        delete_google_calendar_event(
            access_token=resolved_access_token,
            calendar_id=external_calendar_id,
            event_id=external_event_id,
        )

        cancelled_mapping = mark_calendar_event_cancelled(
            organization_id=organization_id,
            reservation_id=reservation_id,
            provider="google",
        )

        return {
            "ok": True,
            "status": "cancelled",
            "reservation_id": reservation_id,
            "external_event_id": external_event_id,
            "mapping": cancelled_mapping,
        }

    except GoogleCalendarServiceError as error:
        failed_mapping = mark_calendar_event_failed(
            organization_id=organization_id,
            reservation_id=reservation_id,
            calendar_id=calendar.get("id") if calendar else None,
            provider="google",
            error_message=str(error),
        )

        return {
            "ok": False,
            "status": "failed",
            "reservation_id": reservation_id,
            "error_message": str(error),
            "mapping": failed_mapping,
        }