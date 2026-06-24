from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.db import supabase


class CalendarIntegrationRepoError(Exception):
    """캘린더 연동 repository 공통 예외입니다."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_calendar_integration(
    organization_id: str,
    provider: str = "google",
    external_calendar_id: str = "primary",
) -> dict[str, Any] | None:
    """
    organization_id + provider + external_calendar_id 기준으로
    캘린더 연동 정보를 조회한다.
    """

    result = (
        supabase.table("calendar_integrations")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("provider", provider)
        .eq("external_calendar_id", external_calendar_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []

    return rows[0] if rows else None


def get_google_calendar_integration(
    organization_id: str,
    external_calendar_id: str = "primary",
) -> dict[str, Any] | None:
    """
    Google Calendar 연동 정보를 조회한다.
    """

    return get_calendar_integration(
        organization_id=organization_id,
        provider="google",
        external_calendar_id=external_calendar_id,
    )


def get_google_access_token(
    organization_id: str,
    external_calendar_id: str = "primary",
) -> str | None:
    """
    Google Calendar access_token을 조회한다.

    현재 단계에서는 refresh token 갱신 로직은 아직 없다.
    access_token이 없거나 status가 connected가 아니면 None을 반환한다.
    """

    integration = get_google_calendar_integration(
        organization_id=organization_id,
        external_calendar_id=external_calendar_id,
    )

    if not integration:
        return None

    if integration.get("status") != "connected":
        return None

    access_token = integration.get("access_token")

    if not access_token:
        return None

    return access_token


def upsert_calendar_integration(
    organization_id: str,
    provider: str = "google",
    external_calendar_id: str = "primary",
    account_email: str | None = None,
    account_name: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_type: str = "Bearer",
    expires_at: str | None = None,
    scopes: list[str] | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """
    캘린더 연동 정보를 생성하거나 갱신한다.

    실제 OAuth 성공 후에는 이 함수를 사용해서
    access_token, refresh_token, expires_at을 저장할 수 있다.
    """

    existing = get_calendar_integration(
        organization_id=organization_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
    )

    data: dict[str, Any] = {
        "organization_id": organization_id,
        "provider": provider,
        "external_calendar_id": external_calendar_id,
        "account_email": account_email,
        "account_name": account_name,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_type,
        "expires_at": expires_at,
        "status": status,
        "updated_at": utc_now_iso(),
    }

    if scopes is not None:
        data["scopes"] = scopes

    if status == "connected":
        data["connected_at"] = utc_now_iso()
        data["disconnected_at"] = None
        data["last_error"] = None

    if existing:
        result = (
            supabase.table("calendar_integrations")
            .update(data)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []

        if not rows:
            raise CalendarIntegrationRepoError(
                "Failed to update calendar integration"
            )

        return rows[0]

    result = supabase.table("calendar_integrations").insert(data).execute()

    rows = result.data or []

    if not rows:
        raise CalendarIntegrationRepoError(
            "Failed to create calendar integration"
        )

    return rows[0]


def mark_calendar_integration_connected(
    organization_id: str,
    provider: str = "google",
    external_calendar_id: str = "primary",
    account_email: str | None = None,
    account_name: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    expires_at: str | None = None,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """
    OAuth 성공 후 캘린더 연동 상태를 connected로 저장한다.
    """

    return upsert_calendar_integration(
        organization_id=organization_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
        account_email=account_email,
        account_name=account_name,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=scopes,
        status="connected",
    )


def mark_calendar_integration_failed(
    organization_id: str,
    error_message: str,
    provider: str = "google",
    external_calendar_id: str = "primary",
) -> dict[str, Any]:
    """
    캘린더 연동 실패 상태를 저장한다.
    """

    existing = get_calendar_integration(
        organization_id=organization_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
    )

    if not existing:
        return upsert_calendar_integration(
            organization_id=organization_id,
            provider=provider,
            external_calendar_id=external_calendar_id,
            status="failed",
        )

    result = (
        supabase.table("calendar_integrations")
        .update(
            {
                "status": "failed",
                "last_error": error_message,
                "updated_at": utc_now_iso(),
            }
        )
        .eq("id", existing["id"])
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise CalendarIntegrationRepoError(
            "Failed to mark calendar integration as failed"
        )

    return rows[0]


def mark_calendar_integration_disconnected(
    organization_id: str,
    provider: str = "google",
    external_calendar_id: str = "primary",
) -> dict[str, Any] | None:
    """
    캘린더 연동을 해제 상태로 변경한다.
    """

    existing = get_calendar_integration(
        organization_id=organization_id,
        provider=provider,
        external_calendar_id=external_calendar_id,
    )

    if not existing:
        return None

    result = (
        supabase.table("calendar_integrations")
        .update(
            {
                "status": "disconnected",
                "access_token": None,
                "refresh_token": None,
                "disconnected_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            }
        )
        .eq("id", existing["id"])
        .execute()
    )

    rows = result.data or []

    return rows[0] if rows else None