from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.repositories.calendar_view_repo import (
    get_calendar_day_view,
    list_calendar_events,
)

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
    

@router.get("/calendar/day")
def get_calendar_day_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    target_date: date = Query(
        ...,
        examples=["2026-07-02"],
    ),
    timezone: str = Query(
        default="Asia/Seoul",
        examples=["Asia/Seoul"],
    ),
    service_id: str | None = Query(
        default=None,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    status: str | None = Query(
        default=None,
        examples=["confirmed"],
    ),
) -> dict[str, Any]:
    """
    하루 캘린더 화면용 데이터를 조회한다.

    service_id가 있으면 예약 가능 슬롯까지 함께 반환한다.
    service_id가 없으면 해당 날짜의 예약 목록만 반환한다.
    """

    try:
        return get_calendar_day_view(
            organization_id=organization_id,
            target_date=target_date,
            timezone=timezone,
            service_id=service_id,
            status=status,
        )

    except Exception as error:
        print("Calendar day API ERROR:", type(error).__name__, str(error))
        raise HTTPException(
            status_code=500,
            detail="Calendar day API error",
        )