from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.repositories.calendar_view_repo import list_calendar_events


router = APIRouter(tags=["Calendar"])


@router.get("/calendar/events")
def list_calendar_events_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    start_date: datetime = Query(
        ...,
        examples=["2026-07-01T00:00:00+09:00"],
    ),
    end_date: datetime = Query(
        ...,
        examples=["2026-08-01T00:00:00+09:00"],
    ),
    status: str | None = Query(default=None, examples=["confirmed"]),
    service_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """
    프론트 캘린더 화면용 예약 이벤트 목록을 조회한다.

    사용 예:
    /calendar/events?organization_id=...&start_date=2026-07-01T00:00:00+09:00&end_date=2026-08-01T00:00:00+09:00
    """

    if start_date >= end_date:
        raise HTTPException(
            status_code=400,
            detail="start_date must be earlier than end_date",
        )

    try:
        events = list_calendar_events(
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
            status=status,
            service_id=service_id,
        )

        return {
            "organization_id": organization_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "count": len(events),
            "events": events,
        }

    except Exception as error:
        print("Calendar events API ERROR:", type(error).__name__, str(error))
        raise HTTPException(
            status_code=500,
            detail="Calendar events API error",
        )