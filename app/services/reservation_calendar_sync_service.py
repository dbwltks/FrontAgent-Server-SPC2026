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
    get_reservation,
    get_service,
)
from app.services.google_calendar_service import (
    GoogleCalendarServiceError,
    create_google_calendar_event,
    delete_google_calendar_event,
)


DEFAULT_GOOGLE_CALENDAR_ID = "primary"


def _resolve_google_access_token(
    organization_id: str,
    external_calendar_id: str = DEFAULT_GOOGLE_CALENDAR_ID,
    access_token: str | None = None,
) -> str | None:
    """
    Google Calendar access_token을 결정합니다.

    우선순위:
    1. 함수 인자로 직접 받은 access_token
    2. calendar_integrations 테이블에 저장된 access_token

    아직 OAuth 연결이 안 되어 있으면 None을 반환합니다.
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
    confirmed 상태의 예약을 Google Calendar에 단방향 push합니다.

    현재 단순화 구조:
    - 우리 예약 원본 = reservations
    - 자체 캘린더 설정 = booking_settings
    - Google 연결 정보 = calendar_integrations
    - Google 이벤트 매핑 = reservation_calendar_events

    아직 OAuth가 없으면 Google API를 호출하지 않고 pending으로 남깁니다.
    """
    reservation = get_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
    )

    if not reservation:
        return {
            "ok": False,
            "status": "failed",
            "reason": "reservation_not_found",
            "reservation_id": reservation_id,
        }

    if reservation.get("status") != "confirmed":
        return {
            "ok": True,
            "status": "skipped",
            "reason": f"reservation_status_is_{reservation.get('status')}",
            "reservation_id": reservation_id,
        }

    external_calendar_id = DEFAULT_GOOGLE_CALENDAR_ID

    resolved_access_token = _resolve_google_access_token(
        organization_id=organization_id,
        external_calendar_id=external_calendar_id,
        access_token=access_token,
    )

    if not resolved_access_token:
        mapping = mark_calendar_event_pending(
            organization_id=organization_id,
            reservation_id=reservation_id,
            provider="google",
            external_calendar_id=external_calendar_id,
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
            provider="google",
            external_calendar_id=external_calendar_id,
            external_event_id=google_event.get("id"),
            external_event_url=google_event.get("htmlLink"),
        )

        return {
            "ok": True,
            "status": "synced",
            "reservation_id": reservation_id,
            "external_calendar_id": external_calendar_id,
            "external_event_id": google_event.get("id"),
            "external_event_url": google_event.get("htmlLink"),
            "mapping": mapping,
        }

    except Exception as error:
        mapping = mark_calendar_event_failed(
            organization_id=organization_id,
            reservation_id=reservation_id,
            provider="google",
            external_calendar_id=external_calendar_id,
            error_message=str(error),
        )

        return {
            "ok": False,
            "status": "failed",
            "reservation_id": reservation_id,
            "external_calendar_id": external_calendar_id,
            "error_message": str(error),
            "mapping": mapping,
        }


def sync_cancelled_reservation_to_calendar(
    organization_id: str,
    reservation_id: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """
    cancelled 상태의 예약과 연결된 Google Calendar 이벤트를 삭제합니다.

    OAuth가 없거나 Google 이벤트 ID가 없으면 실제 삭제 없이 상태만 반환합니다.
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

    external_calendar_id = (
        mapping.get("external_calendar_id")
        or DEFAULT_GOOGLE_CALENDAR_ID
    )
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
            "external_calendar_id": external_calendar_id,
            "external_event_id": external_event_id,
            "mapping": cancelled_mapping,
        }

    except GoogleCalendarServiceError as error:
        failed_mapping = mark_calendar_event_failed(
            organization_id=organization_id,
            reservation_id=reservation_id,
            provider="google",
            external_calendar_id=external_calendar_id,
            error_message=str(error),
        )

        return {
            "ok": False,
            "status": "failed",
            "reservation_id": reservation_id,
            "external_calendar_id": external_calendar_id,
            "error_message": str(error),
            "mapping": failed_mapping,
        }