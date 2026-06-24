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

    weekly_hours: list[dict[str, Any]] | None = Field(default=None)
    exceptions: list[dict[str, Any]] | None = Field(default=None)
    service_policy_overrides: dict[str, Any] | None = Field(default=None)

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
def update_booking_settings(
    organization_id: str,
    request: BookingSettingUpdateRequest,
):
    update_data = request.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    try:
        return update_booking_setting(
            organization_id=organization_id,
            update_data=update_data,
        )
    except BookingSettingRepoError as e:
        raise HTTPException(status_code=400, detail=str(e))