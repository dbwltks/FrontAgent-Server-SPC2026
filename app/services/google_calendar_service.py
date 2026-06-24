from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarServiceError(Exception):
    """Google Calendar 연동 중 발생한 예외입니다."""


def _request_json(
    method: str,
    url: str,
    access_token: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Google Calendar API에 JSON 요청을 보낸다.

    아직 OAuth 저장 구조는 만들지 않았기 때문에 access_token은 외부에서 주입받는다.
    """
    if not access_token:
        raise GoogleCalendarServiceError("Google Calendar access_token이 필요합니다.")

    data = None

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        url=url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=10) as response:
            response_body = response.read().decode("utf-8")

            if not response_body:
                return {}

            return json.loads(response_body)

    except HTTPError as error:
        error_body = error.read().decode("utf-8")

        raise GoogleCalendarServiceError(
            f"Google Calendar API 요청 실패: status={error.code}, body={error_body}"
        ) from error

    except URLError as error:
        raise GoogleCalendarServiceError(
            f"Google Calendar API 네트워크 오류: {error}"
        ) from error


def build_google_calendar_event_body(
    reservation: dict[str, Any],
    service: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    reservations row를 Google Calendar event body로 변환한다.

    reservation.start_at / reservation.end_at은 ISO 문자열이어야 한다.
    """
    customer_name = reservation.get("customer_name") or "고객"
    customer_phone = reservation.get("customer_phone") or ""
    service_name = None

    if service:
        service_name = service.get("name")

    if not service_name:
        service_name = "예약"

    start_at = reservation.get("start_at")
    end_at = reservation.get("end_at")
    timezone = reservation.get("timezone") or "Asia/Seoul"

    if not start_at:
        raise GoogleCalendarServiceError("reservation.start_at이 필요합니다.")

    if not end_at:
        raise GoogleCalendarServiceError("reservation.end_at이 필요합니다.")

    summary = f"[{service_name}] {customer_name}"

    description_lines = [
        f"고객명: {customer_name}",
        f"연락처: {customer_phone}",
        f"예약 ID: {reservation.get('id')}",
        f"예약 상태: {reservation.get('status')}",
    ]

    memo = reservation.get("memo")
    if memo:
        description_lines.append(f"메모: {memo}")

    return {
        "summary": summary,
        "description": "\n".join(description_lines),
        "start": {
            "dateTime": start_at,
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_at,
            "timeZone": timezone,
        },
        "extendedProperties": {
            "private": {
                "front_agent_reservation_id": str(reservation.get("id")),
                "front_agent_organization_id": str(reservation.get("organization_id")),
            }
        },
    }


def create_google_calendar_event(
    access_token: str,
    calendar_id: str,
    reservation: dict[str, Any],
    service: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Google Calendar에 예약 이벤트를 생성한다.

    반환값에는 Google event id, htmlLink 등이 포함된다.
    """
    if not calendar_id:
        raise GoogleCalendarServiceError("calendar_id가 필요합니다.")

    event_body = build_google_calendar_event_body(
        reservation=reservation,
        service=service,
    )

    encoded_calendar_id = quote(calendar_id, safe="")
    url = f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/{encoded_calendar_id}/events"

    return _request_json(
        method="POST",
        url=url,
        access_token=access_token,
        body=event_body,
    )


def delete_google_calendar_event(
    access_token: str,
    calendar_id: str,
    event_id: str,
) -> bool:
    """
    Google Calendar 이벤트를 삭제한다.
    """
    if not calendar_id:
        raise GoogleCalendarServiceError("calendar_id가 필요합니다.")

    if not event_id:
        raise GoogleCalendarServiceError("event_id가 필요합니다.")

    encoded_calendar_id = quote(calendar_id, safe="")
    encoded_event_id = quote(event_id, safe="")

    url = (
        f"{GOOGLE_CALENDAR_API_BASE_URL}"
        f"/calendars/{encoded_calendar_id}/events/{encoded_event_id}"
    )

    _request_json(
        method="DELETE",
        url=url,
        access_token=access_token,
    )

    return True