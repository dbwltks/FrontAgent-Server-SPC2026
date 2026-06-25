from __future__ import annotations

from datetime import datetime
from typing import Any

from datetime import date, time, timedelta
from zoneinfo import ZoneInfo
from app.repositories.reservation_repo import get_available_slots

from app.core.db import supabase


CALENDAR_VISIBLE_STATUSES = [
    "requested",
    "confirmed",
    "cancelled",
    "rejected",
]

def get_calendar_day_view(
    *,
    organization_id: str,
    target_date: date,
    timezone: str = "Asia/Seoul",
    service_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """
    하루 캘린더 화면에 필요한 데이터를 반환한다.

    - reservations 기반 예약 이벤트
    - service_id가 있으면 available-slots도 함께 반환
    """

    tz = ZoneInfo(timezone)

    start_datetime = datetime.combine(
        target_date,
        time.min,
        tzinfo=tz,
    )
    end_datetime = start_datetime + timedelta(days=1)

    reservation_events = list_calendar_events(
        organization_id=organization_id,
        start_date=start_datetime,
        end_date=end_datetime,
        status=status,
        service_id=service_id,
    )

    available_slots: list[dict[str, Any]] = []

    if service_id:
        slot_result = get_available_slots(
            organization_id=organization_id,
            service_id=service_id,
            target_date=target_date,
        )

        if isinstance(slot_result, dict):
            available_slots = slot_result.get("slots", [])
        else:
            available_slots = slot_result or []

    items: list[dict[str, Any]] = []

    for slot in available_slots:
        items.append(
            {
                "type": "available_slot",
                "title": "예약 가능",
                "start": slot.get("start_at") or slot.get("start"),
                "end": slot.get("end_at") or slot.get("end"),
                "status": "available",
                "source": "available_slot",
            }
        )

    for event in reservation_events:
        items.append(
            {
                "type": "reservation",
                "id": event["id"],
                "title": event["title"],
                "start": event["start"],
                "end": event["end"],
                "status": event.get("status"),
                "source": "reservation",
                "reservation_id": event.get("reservation_id"),
                "service_id": event.get("service_id"),
                "service_name": event.get("service_name"),
                "customer_name": event.get("customer_name"),
                "customer_phone": event.get("customer_phone"),
                "customer_email": event.get("customer_email"),
                "memo": event.get("memo"),
            }
        )

    items.sort(key=lambda item: item.get("start") or "")

    return {
        "organization_id": organization_id,
        "date": target_date.isoformat(),
        "timezone": timezone,
        "service_id": service_id,
        "reservation_count": len(reservation_events),
        "available_slot_count": len(available_slots),
        "reservations": reservation_events,
        "available_slots": available_slots,
        "items": items,
    }


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