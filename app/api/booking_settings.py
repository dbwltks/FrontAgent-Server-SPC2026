from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories.booking_setting_repo import (
    BookingSettingRepoError,
    get_or_create_booking_setting,
    update_booking_setting,
)


router = APIRouter(tags=["Booking Settings"])


class BookingSettingUpdateRequest(BaseModel):
    name: str | None = Field(default=None, example="대표 예약 캘린더")
    timezone: str | None = Field(default=None, example="Asia/Seoul")

    slot_interval_minutes: int | None = Field(default=None, example=30)
    min_notice_minutes: int | None = Field(default=None, example=60)
    max_days_ahead: int | None = Field(default=None, example=30)

    requires_approval: bool | None = Field(default=None, example=True)
    allow_customer_cancel: bool | None = Field(default=None, example=True)

    weekly_hours: list[dict[str, Any]] | None = Field(
        default=None,
        example=[
            {
                "day_of_week": 1,
                "start_time": "10:00",
                "end_time": "18:00",
                "is_active": True,
            }
        ],
    )

    exceptions: list[dict[str, Any]] | None = Field(
        default=None,
        example=[
            {
                "date": "2026-07-01",
                "is_closed": True,
                "reason": "휴무",
            }
        ],
    )

    is_active: bool | None = Field(default=None, example=True)


def _handle_booking_setting_error(error: Exception) -> None:
    print("Booking Settings API ERROR:", type(error).__name__, str(error))

    if isinstance(error, BookingSettingRepoError):
        raise HTTPException(status_code=400, detail=str(error))

    raise HTTPException(status_code=500, detail="Booking settings API error")


@router.get("/booking-settings/{organization_id}")
def get_booking_setting_api(
    organization_id: str,
) -> dict[str, Any]:
    """
    조직의 예약 설정을 조회합니다.

    없으면 기본 예약 설정을 자동 생성합니다.
    """
    try:
        setting = get_or_create_booking_setting(organization_id)
        return setting
    except Exception as error:
        _handle_booking_setting_error(error)


@router.patch("/booking-settings/{organization_id}")
def update_booking_setting_api(
    organization_id: str,
    request: BookingSettingUpdateRequest,
) -> dict[str, Any]:
    """
    조직의 예약 설정을 수정합니다.

    사장님이 관리자 화면에서 운영시간, 휴무일, 예약 정책을 수정할 때 사용합니다.
    """
    try:
        update_data = request.model_dump(exclude_unset=True)
        setting = update_booking_setting(
            organization_id=organization_id,
            update_data=update_data,
        )
        return {
            "message": "예약 설정이 수정되었습니다.",
            "booking_setting": setting,
        }
    except Exception as error:
        _handle_booking_setting_error(error)