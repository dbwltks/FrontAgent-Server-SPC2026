from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.db import supabase


CALENDAR_VISIBLE_STATUSES = [
    "requested",
    "confirmed",
    "cancelled",
    "rejected",
]


def list_calendar_events(
    *,
    organization_id: str,
    start_date: datetime,
    end_date: datetime,
    status: str | None = None,
    service_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    프론트 캘린더 화면에 표시할 예약 이벤트 목록을 조회한다.
    reservations를 원본으로 사용하고, services 이름을 붙여서 반환한다.
    """

    query = (
        supabase.table("reservations")
        .select("*")
        .eq("organization_id", organization_id)
        .gte("start_at", start_date.isoformat())
        .lt("start_at", end_date.isoformat())
        .order("start_at", desc=False)
    )

    if status:
        query = query.eq("status", status)
    else:
        query = query.in_("status", CALENDAR_VISIBLE_STATUSES)

    if service_id:
        query = query.eq("service_id", service_id)

    reservation_result = query.execute()
    reservations = reservation_result.data or []

    if not reservations:
        return []

    service_ids = list(
        {
            reservation.get("service_id")
            for reservation in reservations
            if reservation.get("service_id")
        }
    )

    service_map: dict[str, dict[str, Any]] = {}

    if service_ids:
        service_result = (
            supabase.table("services")
            .select("id, name, duration_minutes")
            .eq("organization_id", organization_id)
            .in_("id", service_ids)
            .execute()
        )

        services = service_result.data or []
        service_map = {service["id"]: service for service in services}

    events: list[dict[str, Any]] = []

    for reservation in reservations:
        service = service_map.get(reservation.get("service_id"), {})
        service_name = service.get("name") or "예약"
        customer_name = reservation.get("customer_name") or "고객"

        title = f"{customer_name} - {service_name}"

        events.append(
            {
                "id": reservation["id"],
                "title": title,
                "start": reservation["start_at"],
                "end": reservation["end_at"],
                "status": reservation.get("status"),
                "service_id": reservation.get("service_id"),
                "service_name": service_name,
                "customer_name": reservation.get("customer_name"),
                "customer_phone": reservation.get("customer_phone"),
                "customer_email": reservation.get("customer_email"),
                "memo": reservation.get("memo"),
                "source": "reservation",
                "reservation": reservation,
            }
        )

    return events