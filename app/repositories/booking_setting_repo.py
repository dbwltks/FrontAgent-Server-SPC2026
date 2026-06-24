from __future__ import annotations

from typing import Any

from app.core.db import supabase


class BookingSettingRepoError(Exception):
    """예약 설정 repository 공통 예외입니다."""


def get_booking_setting(organization_id: str) -> dict[str, Any] | None:
    """
    조직의 예약 설정을 조회합니다.

    MVP에서는 조직당 booking_settings 1개만 사용합니다.
    """
    result = (
        supabase.table("booking_settings")
        .select("*")
        .eq("organization_id", organization_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def create_default_booking_setting(organization_id: str) -> dict[str, Any]:
    """
    조직의 기본 예약 설정을 생성합니다.

    문서에서 추출된 설정이 없더라도,
    기본값으로 예약 가능 시간 계산을 시작할 수 있게 합니다.
    """
    insert_data = {
        "organization_id": organization_id,
        "name": "대표 예약 캘린더",
        "timezone": "Asia/Seoul",
        "slot_interval_minutes": 30,
        "min_notice_minutes": 60,
        "max_days_ahead": 30,
        "requires_approval": True,
        "allow_customer_cancel": True,
        "weekly_hours": [],
        "exceptions": [],
        "service_policy_overrides": {},
        "legacy_calendar_ids": [],
        "is_active": True,
    }

    result = supabase.table("booking_settings").insert(insert_data).execute()
    rows = result.data or []

    if not rows:
        raise BookingSettingRepoError("Failed to create default booking setting")

    return rows[0]


def get_or_create_booking_setting(organization_id: str) -> dict[str, Any]:
    """
    booking_settings가 있으면 조회하고,
    없으면 기본 설정을 생성합니다.
    """
    setting = get_booking_setting(organization_id)

    if setting:
        return setting

    return create_default_booking_setting(organization_id)


def update_booking_setting(
    organization_id: str,
    update_data: dict[str, Any],
) -> dict[str, Any]:
    """
    조직의 예약 설정을 수정합니다.

    사장님이 관리자 화면에서 예약 정책/운영시간/휴무일을 수정할 때 사용합니다.
    """
    allowed_fields = {
        "name",
        "timezone",
        "slot_interval_minutes",
        "min_notice_minutes",
        "max_days_ahead",
        "requires_approval",
        "allow_customer_cancel",
        "weekly_hours",
        "exceptions",
        "service_policy_overrides",
        "legacy_calendar_ids",
        "is_active",
    }

    filtered_data = {
        key: value
        for key, value in update_data.items()
        if key in allowed_fields
    }

    if not filtered_data:
        setting = get_or_create_booking_setting(organization_id)
        return setting

    existing = get_booking_setting(organization_id)

    if not existing:
        create_default_booking_setting(organization_id)

    result = (
        supabase.table("booking_settings")
        .update(filtered_data)
        .eq("organization_id", organization_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise BookingSettingRepoError("Failed to update booking setting")

    return rows[0]