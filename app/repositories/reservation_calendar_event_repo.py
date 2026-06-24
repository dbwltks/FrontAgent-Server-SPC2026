from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.db import supabase


class ReservationCalendarEventRepoError(Exception):
    """예약 캘린더 이벤트 매핑 repository 공통 예외입니다."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_calendar_event_mapping(
    organization_id: str,
    reservation_id: str,
    provider: str = "google",
    external_calendar_id: str | None = None,
    external_event_id: str | None = None,
    external_event_url: str | None = None,
    sync_status: str = "pending",
    error_message: str | None = None,
) -> dict[str, Any]:
    """
    예약과 외부 캘린더 이벤트의 매핑 정보를 생성합니다.

    external_calendar_id:
    - Google Calendar ID
    - MVP에서는 기본적으로 "primary" 사용
    """
    insert_data = {
        "organization_id": organization_id,
        "reservation_id": reservation_id,
        "provider": provider,
        "external_calendar_id": external_calendar_id,
        "external_event_id": external_event_id,
        "external_event_url": external_event_url,
        "sync_status": sync_status,
        "error_message": error_message,
    }

    result = (
        supabase.table("reservation_calendar_events")
        .insert(insert_data)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ReservationCalendarEventRepoError(
            "Failed to create reservation calendar event mapping"
        )

    return rows[0]


def get_calendar_event_mapping_by_reservation(
    organization_id: str,
    reservation_id: str,
    provider: str = "google",
) -> dict[str, Any] | None:
    """
    reservation_id 기준으로 외부 캘린더 이벤트 매핑을 조회합니다.
    """
    result = (
        supabase.table("reservation_calendar_events")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("reservation_id", reservation_id)
        .eq("provider", provider)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    rows = result.data or []

    return rows[0] if rows else None


def mark_calendar_event_pending(
    organization_id: str,
    reservation_id: str,
    provider: str = "google",
    external_calendar_id: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """
    Google Calendar 연동 대상이지만 아직 이벤트 생성 전인 상태를 pending으로 저장합니다.
    기존 매핑이 있으면 갱신하고, 없으면 생성합니다.
    """
    existing = get_calendar_event_mapping_by_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
    )

    update_data = {
        "external_calendar_id": external_calendar_id,
        "sync_status": "pending",
        "error_message": error_message,
        "updated_at": utc_now_iso(),
    }

    if existing:
        result = (
            supabase.table("reservation_calendar_events")
            .update(update_data)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []

        if not rows:
            raise ReservationCalendarEventRepoError(
                "Failed to mark calendar event as pending"
            )

        return rows[0]

    return create_calendar_event_mapping(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
        sync_status="pending",
        error_message=error_message,
    )


def mark_calendar_event_synced(
    organization_id: str,
    reservation_id: str,
    external_event_id: str,
    external_event_url: str | None = None,
    provider: str = "google",
    external_calendar_id: str | None = None,
) -> dict[str, Any]:
    """
    Google Calendar 이벤트 생성 성공 후 매핑 정보를 synced 상태로 갱신합니다.
    기존 매핑이 없으면 새로 생성합니다.
    """
    existing = get_calendar_event_mapping_by_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
    )

    update_data = {
        "external_calendar_id": external_calendar_id,
        "external_event_id": external_event_id,
        "external_event_url": external_event_url,
        "sync_status": "synced",
        "error_message": None,
        "updated_at": utc_now_iso(),
    }

    if existing:
        result = (
            supabase.table("reservation_calendar_events")
            .update(update_data)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []

        if not rows:
            raise ReservationCalendarEventRepoError(
                "Failed to mark calendar event as synced"
            )

        return rows[0]

    return create_calendar_event_mapping(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
        external_event_id=external_event_id,
        external_event_url=external_event_url,
        sync_status="synced",
    )


def mark_calendar_event_failed(
    organization_id: str,
    reservation_id: str,
    error_message: str,
    provider: str = "google",
    external_calendar_id: str | None = None,
) -> dict[str, Any]:
    """
    Google Calendar 이벤트 생성/수정/삭제 실패 시 failed 상태로 저장합니다.
    """
    existing = get_calendar_event_mapping_by_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
    )

    update_data = {
        "external_calendar_id": external_calendar_id,
        "sync_status": "failed",
        "error_message": error_message,
        "updated_at": utc_now_iso(),
    }

    if existing:
        result = (
            supabase.table("reservation_calendar_events")
            .update(update_data)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []

        if not rows:
            raise ReservationCalendarEventRepoError(
                "Failed to mark calendar event as failed"
            )

        return rows[0]

    return create_calendar_event_mapping(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
        sync_status="failed",
        error_message=error_message,
    )


def mark_calendar_event_cancelled(
    organization_id: str,
    reservation_id: str,
    provider: str = "google",
) -> dict[str, Any] | None:
    """
    예약 취소 후 외부 캘린더 이벤트 매핑 상태를 cancelled로 변경합니다.
    """
    existing = get_calendar_event_mapping_by_reservation(
        organization_id=organization_id,
        reservation_id=reservation_id,
        provider=provider,
    )

    if not existing:
        return None

    result = (
        supabase.table("reservation_calendar_events")
        .update(
            {
                "sync_status": "cancelled",
                "error_message": None,
                "updated_at": utc_now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", existing["id"])
        .execute()
    )

    rows = result.data or []

    return rows[0] if rows else None