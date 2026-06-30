from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.db import supabase
from app.repositories.booking_setting_repo import get_or_create_booking_setting
from app.repositories.service_repo import calculate_service_price, get_service_item

RESERVATION_ACTIVE_STATUSES = ["requested", "confirmed"]


class ReservationRepoError(Exception):
    """예약 도메인 repository 공통 예외입니다."""


class NotFoundError(ReservationRepoError):
    """필요한 데이터를 찾지 못했을 때 사용합니다."""


class ReservationConflictError(ReservationRepoError):
    """예약 시간이 기존 예약과 겹칠 때 사용합니다."""


def list_services(organization_id: str) -> list[dict]:
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .eq("approval_status", "approved")
        .order("created_at")
        .execute()
    )

    return result.data or []


def get_service(organization_id: str, service_id: str) -> dict:
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", service_id)
        .eq("is_active", True)
        .eq("approval_status", "approved")
        .limit(1)
        .execute()
    )

    if not result.data:
        raise NotFoundError("서비스를 찾을 수 없습니다.")

    return result.data[0]


def create_service(
    organization_id: str,
    name: str,
    duration_minutes: int,
    description: str | None = None,
    is_active: bool = True,
    is_reservable: bool = True,
) -> dict:
    """
    예약 가능한 서비스를 생성합니다.

    관리자 화면에서 사장님이 새 예약 서비스를 등록할 때 사용합니다.
    """
    if not name or not name.strip():
        raise ReservationRepoError("Service name is required")

    if duration_minutes <= 0:
        raise ReservationRepoError("duration_minutes must be greater than 0")

    insert_data = {
        "organization_id": organization_id,
        "name": name.strip(),
        "duration_minutes": duration_minutes,
        "description": description,
        "is_active": is_active,
        "is_reservable": is_reservable,
    }

    result = supabase.table("services").insert(insert_data).execute()
    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to create service")

    return rows[0]


def update_service(
    organization_id: str,
    service_id: str,
    update_data: dict,
) -> dict:
    """
    예약 서비스를 수정합니다.

    수정 가능한 값:
    - name
    - description
    - duration_minutes
    - is_active
    - is_reservable
    """
    service = get_service(
        organization_id=organization_id,
        service_id=service_id,
    )

    if not service:
        raise NotFoundError("Service not found")

    allowed_fields = {
        "name",
        "description",
        "duration_minutes",
        "is_active",
        "is_reservable",
    }

    data = {
        key: value
        for key, value in update_data.items()
        if key in allowed_fields and value is not None
    }

    if not data:
        raise ReservationRepoError("No fields to update")

    if "name" in data and not str(data["name"]).strip():
        raise ReservationRepoError("Service name is required")

    if "name" in data:
        data["name"] = str(data["name"]).strip()

    if "duration_minutes" in data and int(data["duration_minutes"]) <= 0:
        raise ReservationRepoError("duration_minutes must be greater than 0")

    result = (
        supabase.table("services")
        .update(data)
        .eq("organization_id", organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to update service")

    return rows[0]


def delete_service(
    organization_id: str,
    service_id: str,
) -> dict:
    """
    예약 서비스를 삭제합니다.

    실제 삭제하지 않고 비활성화합니다.
    이유:
    - 기존 reservations.service_id 참조 보존
    - 과거 예약 내역 조회 가능
    - 신규 예약 목록에서는 제외
    """
    service = get_service(
        organization_id=organization_id,
        service_id=service_id,
    )

    if not service:
        raise NotFoundError("Service not found")

    result = (
        supabase.table("services")
        .update(
            {
                "is_active": False,
                "is_reservable": False,
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", service_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to delete service")

    return rows[0]




def find_conflicting_reservations(
    organization_id: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict]:
    """
    새 예약 시간과 겹치는 기존 예약을 조회합니다.

    단순화 구조에서는 조직당 자체 캘린더 1개를 사용하므로
    calendar_id 조건을 제거합니다.
    """
    result = (
        supabase.table("reservations")
        .select("*")
        .eq("organization_id", organization_id)
        .in_("status", RESERVATION_ACTIVE_STATUSES)
        .lt("start_at", end_at.isoformat())
        .gt("end_at", start_at.isoformat())
        .execute()
    )

    return result.data or []


def get_available_slots(
    organization_id: str,
    service_id: str,
    target_date: date,
    service_item_id: str | None = None,
) -> dict:
    """
    예약 가능한 시간 목록을 계산합니다.

    처리 순서:
    1. service 조회
    2. booking_settings 조회
    3. exceptions 반영
    4. weekly_hours 조회
    5. 기존 reservations와 충돌 제거
    6. 가능한 slot 반환
    """
    service = get_service(organization_id, service_id)

    if not service:
        raise NotFoundError("Service not found")

    setting = get_or_create_booking_setting(organization_id)

    if not setting.get("is_active", True):
        return {
            "service_id": service_id,
            "date": target_date.isoformat(),
            "timezone": setting.get("timezone") or "Asia/Seoul",
            "slots": [],
        }

    timezone_name = setting.get("timezone") or "Asia/Seoul"
    timezone = ZoneInfo(timezone_name)

    # 소요시간은 service(대분류)가 아니라 실제 예약 단위인 service_item(세부
    # 항목)마다 다르다(예: 이사 청소 180분, 화장실 청소 60분). service_item_id가
    # 있으면 그 항목의 duration을 우선 쓰고, 대분류의 duration_minutes는 더
    # 이상 필수가 아니므로(세부항목 단위 예약으로 전환) None이면 기본값으로
    # 폴백한다 - 과거에는 대분류 자체에 duration이 있는 게 전제였다.
    duration_minutes = None
    if service_item_id:
        service_item = get_service_item(organization_id, service_item_id)
        if service_item and service_item.get("duration_minutes") is not None:
            duration_minutes = int(service_item["duration_minutes"])

    if duration_minutes is None:
        raw_duration = service.get("duration_minutes")
        duration_minutes = int(raw_duration) if raw_duration is not None else 60
    slot_interval_minutes = int(setting.get("slot_interval_minutes") or 30)

    exception = _find_exception_for_date(
        exceptions=setting.get("exceptions") or [],
        target_date=target_date,
    )

    if exception and exception.get("is_closed") is True:
        return {
            "service_id": service_id,
            "date": target_date.isoformat(),
            "timezone": timezone_name,
            "slots": [],
            "reason": exception.get("reason") or "closed",
        }

    rules = _get_rules_for_date(
        setting=setting,
        target_date=target_date,
        exception=exception,
    )

    slots: list[dict] = []

    for rule in rules:
        if rule.get("is_active") is False:
            continue

        current_start = _combine_date_and_time(
            target_date,
            _parse_time(rule["start_time"]),
            timezone,
        )
        rule_end = _combine_date_and_time(
            target_date,
            _parse_time(rule["end_time"]),
            timezone,
        )

        while current_start + timedelta(minutes=duration_minutes) <= rule_end:
            current_end = current_start + timedelta(minutes=duration_minutes)

            if _is_allowed_by_notice_policy(
                target_date=target_date,
                slot_start=current_start,
                setting=setting,
                timezone=timezone,
            ):
                conflicts = find_conflicting_reservations(
                    organization_id=organization_id,
                    start_at=current_start,
                    end_at=current_end,
                )

                if not conflicts:
                    slots.append(
                        {
                            "start_at": current_start.isoformat(),
                            "end_at": current_end.isoformat(),
                        }
                    )

            current_start += timedelta(minutes=slot_interval_minutes)

    return {
        "service_id": service_id,
        "date": target_date.isoformat(),
        "timezone": timezone_name,
        "slots": slots,
    }


def create_or_get_customer(
    organization_id: str,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict:
    """
    고객 정보를 생성하거나 기존 고객을 조회합니다.

    우선순위:
    1. phone이 있으면 organization_id + phone 기준으로 조회
    2. 없으면 새 고객 생성
    """
    if phone:
        result = (
            supabase.table("customers")
            .select("*")
            .eq("organization_id", organization_id)
            .eq("phone", phone)
            .limit(1)
            .execute()
        )

        rows = result.data or []

        if rows:
            customer = rows[0]
            update_data: dict = {}

            if name and not customer.get("name"):
                update_data["name"] = name

            if email and not customer.get("email"):
                update_data["email"] = email

            if update_data:
                update_result = (
                    supabase.table("customers")
                    .update(update_data)
                    .eq("id", customer["id"])
                    .execute()
                )

                updated_rows = update_result.data or []
                return updated_rows[0] if updated_rows else customer

            return customer

    insert_data = {
        "organization_id": organization_id,
        "name": name,
        "phone": phone,
        "email": email,
        "is_guest": True,
    }

    result = supabase.table("customers").insert(insert_data).execute()
    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to create customer")

    return rows[0]


def create_reservation(
    organization_id: str,
    service_id: str,
    customer_name: str | None,
    customer_phone: str | None,
    customer_email: str | None,
    start_at: datetime,
    end_at: datetime | None = None,
    conversation_id: str | None = None,
    source_channel: str = "web_chat",
    memo: str | None = None,
    created_by: str = "ai",
    service_item_id: str | None = None,
    selected_option_ids: list[str] | None = None,
) -> dict:
    """
    예약 요청을 생성합니다.

    기본 상태는 requested입니다.
    확정은 confirm_reservation()에서 처리합니다.

    service_item_id가 있으면 서비스 아이템/옵션 기준으로 가격과 소요 시간을 계산하고,
    reservations에 service_item_id, selected_options, total_price, ordered_summary를 저장합니다.
    """
    selected_option_ids = selected_option_ids or []

    if selected_option_ids and not service_item_id:
        raise ReservationRepoError(
            "service_item_id is required when selected_option_ids are provided"
        )

    service = get_service(organization_id, service_id)

    if not service:
        raise NotFoundError("Service not found")

    if service.get("is_active") is False or service.get("is_reservable") is False:
        raise ReservationRepoError("Service is not reservable")

    price_result: dict | None = None

    if service_item_id:
        try:
            price_result = calculate_service_price(
                organization_id=organization_id,
                service_item_id=service_item_id,
                option_ids=selected_option_ids,
            )
        except ValueError as exc:
            raise ReservationRepoError(str(exc))

        if end_at is None:
            total_duration_minutes = int(
                price_result.get("total_duration_minutes") or 0
            )

            if total_duration_minutes <= 0:
                raise ReservationRepoError(
                    "total_duration_minutes must be greater than 0"
                )

            end_at = start_at + timedelta(minutes=total_duration_minutes)

    if end_at is None:
        service_duration_minutes = service.get("duration_minutes")

        if service_duration_minutes is None:
            raise ReservationRepoError(
                "end_at is required when service duration is missing"
            )

        end_at = start_at + timedelta(minutes=int(service_duration_minutes))

    if start_at >= end_at:
        raise ReservationRepoError("start_at must be earlier than end_at")

    setting = get_or_create_booking_setting(organization_id)

    conflicts = find_conflicting_reservations(
        organization_id=organization_id,
        start_at=start_at,
        end_at=end_at,
    )

    if conflicts:
        raise ReservationConflictError(
            "Reservation time conflicts with existing reservation"
        )

    customer = create_or_get_customer(
        organization_id=organization_id,
        name=customer_name,
        phone=customer_phone,
        email=customer_email,
    )

    insert_data = {
        "organization_id": organization_id,
        "conversation_id": conversation_id,
        "customer_id": customer["id"],
        "service_id": service_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "timezone": setting.get("timezone") or "Asia/Seoul",
        "status": "requested",
        "source_channel": source_channel,
        "memo": memo,
        "created_by": created_by,
    }

    if price_result:
        insert_data.update(
            {
                "service_item_id": service_item_id,
                "selected_options": price_result["ordered_summary"].get("options", []),
                "total_price": price_result.get("total_price", 0),
                "ordered_summary": price_result.get("ordered_summary", {}),
            }
        )

    result = supabase.table("reservations").insert(insert_data).execute()
    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to create reservation")

    return rows[0]


def list_reservations(
    organization_id: str,
    status: str | None = None,
    service_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    customer_phone: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    예약 목록을 조회합니다.

    관리자 화면과 Swagger 테스트에서 사용합니다.
    """
    query = (
        supabase.table("reservations")
        .select("*")
        .eq("organization_id", organization_id)
        .order("start_at", desc=False)
        .limit(limit)
    )

    if status:
        query = query.eq("status", status)

    if service_id:
        query = query.eq("service_id", service_id)

    if date_from:
        query = query.gte("start_at", date_from.isoformat())

    if date_to:
        query = query.lt("start_at", date_to.isoformat())

    if customer_phone:
        query = query.eq("customer_phone", customer_phone)

    result = query.execute()
    return result.data or []


def get_reservation(
    organization_id: str,
    reservation_id: str,
) -> dict | None:
    """
    예약 상세를 조회합니다.
    """
    result = (
        supabase.table("reservations")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", reservation_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def confirm_reservation(
    organization_id: str,
    reservation_id: str,
) -> dict:
    """
    예약을 확정 상태로 변경합니다.
    확정 직전에도 다시 충돌을 확인합니다.
    """
    reservation = get_reservation(organization_id, reservation_id)

    if not reservation:
        raise NotFoundError("Reservation not found")

    if reservation["status"] == "confirmed":
        return reservation

    if reservation["status"] not in ["requested"]:
        raise ReservationRepoError(
            f"Cannot confirm reservation with status: {reservation['status']}"
        )

    start_at = _parse_datetime(reservation["start_at"])
    end_at = _parse_datetime(reservation["end_at"])

    conflicts = find_conflicting_reservations(
        organization_id=organization_id,
        start_at=start_at,
        end_at=end_at,
    )

    conflicts = [
        conflict
        for conflict in conflicts
        if conflict["id"] != reservation_id
    ]

    if conflicts:
        raise ReservationConflictError(
            "Reservation time conflicts with existing reservation"
        )

    result = (
        supabase.table("reservations")
        .update(
            {
                "status": "confirmed",
                "confirmed_at": datetime.now(
                    tz=ZoneInfo("Asia/Seoul")
                ).isoformat(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", reservation_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to confirm reservation")

    return rows[0]


def reject_reservation(
    organization_id: str,
    reservation_id: str,
) -> dict:
    """
    예약 요청을 거절 상태로 변경합니다.
    """
    reservation = get_reservation(organization_id, reservation_id)

    if not reservation:
        raise NotFoundError("Reservation not found")

    if reservation["status"] not in ["requested"]:
        raise ReservationRepoError(
            f"Cannot reject reservation with status: {reservation['status']}"
        )

    result = (
        supabase.table("reservations")
        .update({"status": "rejected"})
        .eq("organization_id", organization_id)
        .eq("id", reservation_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to reject reservation")

    return rows[0]


def cancel_reservation(
    organization_id: str,
    reservation_id: str,
) -> dict:
    """
    예약을 취소 상태로 변경합니다.
    """
    reservation = get_reservation(organization_id, reservation_id)

    if not reservation:
        raise NotFoundError("Reservation not found")

    if reservation["status"] in ["cancelled", "rejected", "completed", "no_show"]:
        raise ReservationRepoError(
            f"Cannot cancel reservation with status: {reservation['status']}"
        )

    result = (
        supabase.table("reservations")
        .update(
            {
                "status": "cancelled",
                "cancelled_at": datetime.now(
                    tz=ZoneInfo("Asia/Seoul")
                ).isoformat(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", reservation_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ReservationRepoError("Failed to cancel reservation")

    return rows[0]


def _find_exception_for_date(
    exceptions: list[dict],
    target_date: date,
) -> dict | None:
    target_date_text = target_date.isoformat()

    for exception in exceptions:
        if exception.get("date") == target_date_text:
            return exception

    return None


def _get_rules_for_date(
    setting: dict,
    target_date: date,
    exception: dict | None = None,
) -> list[dict]:
    """
    특정 날짜에 적용할 운영시간 규칙을 반환합니다.

    우선순위:
    1. 특정일 예외 운영시간
    2. weekly_hours 반복 운영시간
    """
    if exception and exception.get("is_closed") is False:
        start_time = exception.get("start_time")
        end_time = exception.get("end_time")

        if start_time and end_time:
            return [
                {
                    "day_of_week": None,
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_active": True,
                }
            ]

    # Python: Monday=0, Sunday=6
    # DB 문서 기준: Sunday=0, Monday=1 ... Saturday=6
    day_of_week = (target_date.weekday() + 1) % 7

    rules = []

    for rule in setting.get("weekly_hours") or []:
        if int(rule.get("day_of_week")) == day_of_week:
            rules.append(rule)

    return rules


def _is_allowed_by_notice_policy(
    target_date: date,
    slot_start: datetime,
    setting: dict,
    timezone: ZoneInfo,
) -> bool:
    """
    최소 예약 가능 시간과 최대 예약 가능 기간을 검사합니다.
    """
    now = datetime.now(tz=timezone)

    min_notice_minutes = int(setting.get("min_notice_minutes") or 0)
    max_days_ahead = int(setting.get("max_days_ahead") or 365)

    if slot_start < now + timedelta(minutes=min_notice_minutes):
        return False

    max_date = now.date() + timedelta(days=max_days_ahead)

    if target_date > max_date:
        return False

    return True


def _parse_time(value: str) -> time:
    """
    Supabase time 값을 Python time으로 변환합니다.

    예:
    - '10:00:00'
    - '10:00'
    """
    if len(value) == 5:
        return time.fromisoformat(value)

    return time.fromisoformat(value[:8])


def _parse_datetime(value: str) -> datetime:
    """
    Supabase timestamptz 문자열을 Python datetime으로 변환합니다.
    """
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _combine_date_and_time(
    target_date: date,
    target_time: time,
    timezone: ZoneInfo,
) -> datetime:
    """
    date + time + timezone을 합쳐 timezone-aware datetime으로 만듭니다.
    """
    return datetime.combine(target_date, target_time, tzinfo=timezone)