from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from supabase import create_client

from app.core.config import settings

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.repositories.reservation_repo import (
    NotFoundError,
    ReservationConflictError,
    ReservationRepoError,
    create_reservation as repo_create_reservation,
    get_available_slots as repo_get_available_slots,
    get_service as repo_get_service,
    list_services as repo_list_services,
)

TaskFunctionHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass
class RegisteredTaskFunction:
    name: str
    handler: TaskFunctionHandler
    description: str = ""

def get_supabase_client():
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )
   
def _get_value(
    params: dict[str, Any],
    variables: dict[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    """
    Function Node params 값을 우선 사용하고,
    없으면 task memory variables에서 찾는다.
    """
    for key in keys:
        value = params.get(key)
        if value is not None and value != "":
            return value

        value = variables.get(key)
        if value is not None and value != "":
            return value

    return default


def _parse_date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()

    return date.fromisoformat(text)


def _parse_time_value(value: Any) -> time:
    if isinstance(value, time):
        return value

    text = str(value).strip()

    # "15:00:00" / "15:00" 둘 다 처리 가능
    return time.fromisoformat(text)


def _parse_datetime_value(value: Any, timezone_name: str = "Asia/Seoul") -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(timezone_name))
        return value

    text = str(value).strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))

    return parsed


def _build_start_end(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> tuple[datetime | None, datetime | None]:
    timezone_name = _get_value(
        params,
        variables,
        "timezone",
        default="Asia/Seoul",
    )

    start_at_value = _get_value(params, variables, "start_at")
    end_at_value = _get_value(params, variables, "end_at")

    if start_at_value:
        start_at = _parse_datetime_value(start_at_value, timezone_name)
    else:
        date_value = _get_value(
            params,
            variables,
            "date",
            "target_date",
            "normalized_date",
            "reservation_date",
        )
        time_value = _get_value(
            params,
            variables,
            "time",
            "normalized_time",
            "reservation_time",
        )

        if not date_value or not time_value:
            return None, None

        start_at = datetime.combine(
            _parse_date_value(date_value),
            _parse_time_value(time_value),
            tzinfo=ZoneInfo(timezone_name),
        )

    if end_at_value:
        end_at = _parse_datetime_value(end_at_value, timezone_name)
        return start_at, end_at

    duration_minutes = _get_value(
        params,
        variables,
        "duration_minutes",
    )

    if not duration_minutes:
        organization_id = _get_value(params, variables, "organization_id")
        service_id = _get_value(params, variables, "service_id")

        if organization_id and service_id:
            service = repo_get_service(
                organization_id=organization_id,
                service_id=service_id,
            )
            duration_minutes = service.get("duration_minutes")

    if not duration_minutes:
        return start_at, None

    end_at = start_at + timedelta(minutes=int(duration_minutes))

    return start_at, end_at


def _domain_error_result(error: Exception) -> dict[str, Any]:
    if isinstance(error, NotFoundError):
        return {
            "ok": False,
            "error_code": "not_found",
            "message": str(error),
        }

    if isinstance(error, ReservationConflictError):
        return {
            "ok": False,
            "error_code": "reservation_conflict",
            "message": str(error),
        }

    if isinstance(error, ReservationRepoError):
        return {
            "ok": False,
            "error_code": "reservation_repo_error",
            "message": str(error),
        }

    return {
        "ok": False,
        "error_code": "unknown_error",
        "message": str(error),
    }


def check_required_variables(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    memory에 필요한 값들이 모두 있는지 확인한다.
    """

    required_keys = params.get("required_keys") or []

    missing_keys = [
        key
        for key in required_keys
        if variables.get(key) is None or variables.get(key) == ""
    ]

    return {
        "all_present": len(missing_keys) == 0,
        "missing_keys": missing_keys,
    }


def check_reservation_availability(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    MVP용 예약 가능 여부 확인 함수.

    실제 예약 테이블이 연결되기 전까지는
    날짜와 시간이 있으면 available=true로 판단한다.
    """

    date = params.get("date")
    time = params.get("time")
    party_size = params.get("party_size")

    if not date or not time:
        return {
            "available": False,
            "reason": "date_or_time_missing",
            "date": date,
            "time": time,
            "party_size": party_size,
            "source": "function_registry",
        }

    return {
        "available": True,
        "reason": None,
        "date": date,
        "time": time,
        "party_size": party_size,
        "source": "function_registry",
    }


def create_reservation_request(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    MVP용 예약 요청 생성 함수.

    실제 예약 확정 DB 저장 전 단계에서는
    memory에 예약 요청 결과를 남기는 역할을 한다.
    """

    customer_name = params.get("customer_name")
    date = params.get("date")
    time = params.get("time")
    party_size = params.get("party_size")

    missing_keys = []

    if not customer_name:
        missing_keys.append("customer_name")
    if not date:
        missing_keys.append("date")
    if not time:
        missing_keys.append("time")

    if missing_keys:
        return {
            "created": False,
            "status": "missing_required_fields",
            "missing_keys": missing_keys,
            "reservation": None,
        }

    reservation = {
        "customer_name": customer_name,
        "date": date,
        "time": time,
        "party_size": party_size,
        "status": "requested",
    }

    return {
        "created": True,
        "status": "requested",
        "reservation": reservation,
    }


def reservation_list_services(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    Function Node용 서비스 목록 조회 함수.
    Task Flow에서 고객에게 예약 가능한 상품/서비스를 보여줄 때 사용한다.
    """
    organization_id = _get_value(params, variables, "organization_id")

    if not organization_id:
        return {
            "ok": False,
            "error_code": "organization_id_missing",
            "message": "organization_id가 필요합니다.",
            "services": [],
            "count": 0,
        }

    try:
        services = repo_list_services(organization_id=organization_id)

        return {
            "ok": True,
            "services": services,
            "count": len(services),
        }

    except Exception as error:
        result = _domain_error_result(error)
        result.update(
            {
                "services": [],
                "count": 0,
            }
        )
        return result


def reservation_get_available_slots(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    Function Node용 예약 가능 시간 조회 함수.
    date만 있으면 가능한 slot 목록을 반환하고,
    time까지 있으면 해당 시간이 가능한지도 함께 판단한다.
    """
    organization_id = _get_value(params, variables, "organization_id")
    service_id = _get_value(params, variables, "service_id")
    calendar_id = _get_value(params, variables, "calendar_id")
    target_date_value = _get_value(
        params,
        variables,
        "date",
        "target_date",
        "normalized_date",
        "reservation_date",
    )
    target_time_value = _get_value(
        params,
        variables,
        "time",
        "normalized_time",
        "reservation_time",
    )

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")
    if not service_id:
        missing_keys.append("service_id")
    if not target_date_value:
        missing_keys.append("date")

    if missing_keys:
        return {
            "ok": False,
            "available": False,
            "is_available": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "slots": [],
            "slot_count": 0,
        }

    try:
        target_date = _parse_date_value(target_date_value)

        slot_result = repo_get_available_slots(
            organization_id=organization_id,
            service_id=service_id,
            target_date=target_date,
            calendar_id=calendar_id,
        )

        slots = slot_result.get("slots") or []

        selected_slot = None

        if target_time_value:
            target_time = _parse_time_value(target_time_value).strftime("%H:%M")

            for slot in slots:
                slot_start_at = slot.get("start_at")
                if not slot_start_at:
                    continue

                slot_start_time = _parse_datetime_value(
                    slot_start_at,
                    slot_result.get("timezone") or "Asia/Seoul",
                ).strftime("%H:%M")

                if slot_start_time == target_time:
                    selected_slot = slot
                    break

            is_available = selected_slot is not None

        else:
            is_available = len(slots) > 0

        return {
            "ok": True,
            "available": is_available,
            "is_available": is_available,
            "selected_slot": selected_slot,
            "slots": slots,
            "slot_count": len(slots),
            "service_id": slot_result.get("service_id"),
            "calendar_id": slot_result.get("calendar_id"),
            "date": slot_result.get("date"),
            "timezone": slot_result.get("timezone"),
        }

    except Exception as error:
        result = _domain_error_result(error)
        result.update(
            {
                "available": False,
                "is_available": False,
                "selected_slot": None,
                "slots": [],
                "slot_count": 0,
            }
        )
        return result


def reservation_create_reservation(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    Function Node용 예약 요청 생성 함수.
    실제 reservations 테이블에 status=requested 예약을 생성한다.
    """
    organization_id = _get_value(params, variables, "organization_id")
    conversation_id = _get_value(params, variables, "conversation_id")
    service_id = _get_value(params, variables, "service_id")
    calendar_id = _get_value(params, variables, "calendar_id")

    customer_name = _get_value(params, variables, "customer_name", "name")
    customer_phone = _get_value(params, variables, "customer_phone", "phone")
    customer_email = _get_value(params, variables, "customer_email", "email")

    source_channel = _get_value(
        params,
        variables,
        "source_channel",
        default="web_chat",
    )
    memo = _get_value(params, variables, "memo")

    start_at, end_at = _build_start_end(params, variables)

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")
    if not service_id:
        missing_keys.append("service_id")
    if not customer_name:
        missing_keys.append("customer_name")
    if not customer_phone:
        missing_keys.append("customer_phone")
    if not start_at:
        missing_keys.append("start_at")
    if not end_at:
        missing_keys.append("end_at")

    if missing_keys:
        return {
            "ok": False,
            "created": False,
            "status": "missing_required_fields",
            "missing_keys": missing_keys,
            "reservation_id": None,
            "reservation": None,
        }

    try:
        reservation = repo_create_reservation(
            organization_id=organization_id,
            conversation_id=conversation_id,
            service_id=service_id,
            calendar_id=calendar_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            start_at=start_at,
            end_at=end_at,
            source_channel=source_channel,
            memo=memo,
        )

        return {
            "ok": True,
            "created": True,
            "status": reservation.get("status"),
            "reservation_id": reservation.get("id"),
            "reservation": reservation,
        }

    except Exception as error:
        result = _domain_error_result(error)
        result.update(
            {
                "created": False,
                "status": "failed",
                "reservation_id": None,
                "reservation": None,
            }
        )
        return result


def lookup_cancelable_reservations(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    취소 가능한 예약 목록을 조회한다.

    검색 기준:
    - organization_id: 필수
    - customer_name: 선택
    - customer_phone: 선택
    - reservation_id: 선택
    - product_name: 선택

    반환:
    - count: 조회된 예약 수
    - reservations: 취소 가능한 예약 목록
    """

    organization_id = (
        params.get("organization_id")
        or variables.get("organization_id")
    )

    customer_name = (
        params.get("customer_name")
        or variables.get("customer_name")
        or variables.get("name")
    )

    customer_phone = (
        params.get("customer_phone")
        or variables.get("customer_phone")
        or variables.get("phone")
    )

    reservation_id = (
        params.get("reservation_id")
        or variables.get("reservation_id")
    )

    product_name = (
        params.get("product_name")
        or variables.get("product_name")
    )

    if not organization_id:
        return {
            "ok": False,
            "count": 0,
            "reservations": [],
            "message": "organization_id가 필요합니다.",
            "error_code": "ORGANIZATION_ID_MISSING",
        }

    if not any([customer_name, customer_phone, reservation_id, product_name]):
        return {
            "ok": False,
            "count": 0,
            "reservations": [],
            "message": "예약 조회를 위해 이름, 전화번호, 예약 ID, 상품명 중 하나가 필요합니다.",
            "error_code": "SEARCH_CONDITION_MISSING",
        }

    supabase = get_supabase_client()

    query = (
        supabase
        .table("reservations")
        .select(
            "id, customer_name, customer_phone, product_name, "
            "scheduled_start_at, scheduled_end_at, status"
        )
        .eq("organization_id", organization_id)
        .in_("status", ["reserved", "confirmed"])
        .order("scheduled_start_at")
    )

    if reservation_id:
        query = query.eq("id", reservation_id)

    if customer_name:
        query = query.ilike("customer_name", f"%{customer_name}%")

    if customer_phone:
        query = query.eq("customer_phone", customer_phone)

    if product_name:
        query = query.ilike("product_name", f"%{product_name}%")

    response = query.execute()

    reservations = response.data or []

    options = []
    for index, reservation in enumerate(reservations, start=1):
        options.append(
            {
                "option_no": index,
                "reservation_id": reservation.get("id"),
                "product_name": reservation.get("product_name"),
                "scheduled_start_at": reservation.get("scheduled_start_at"),
                "scheduled_end_at": reservation.get("scheduled_end_at"),
                "status": reservation.get("status"),
            }
        )

    return {
        "ok": True,
        "count": len(reservations),
        "reservations": reservations,
        "options": options,
        "message": build_cancelable_reservation_message(options),
    }

def cancel_reservation(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """
    예약을 실제로 취소한다.

    입력 우선순위:
    1. reservation_id
    2. selected_reservation_id
    3. selected_option_no + options

    처리:
    - reservations.status를 cancelled로 변경
    - cancel_reason 저장
    - cancelled_at 저장
    """

    organization_id = (
        params.get("organization_id")
        or variables.get("organization_id")
    )

    reservation_id = (
        params.get("reservation_id")
        or params.get("selected_reservation_id")
        or variables.get("reservation_id")
        or variables.get("selected_reservation_id")
    )

    selected_option_no = (
        params.get("selected_option_no")
        or params.get("option_no")
        or variables.get("selected_option_no")
        or variables.get("option_no")
    )

    options = (
        params.get("options")
        or variables.get("options")
        or variables.get("cancel_options")
        or []
    )

    cancel_reason = (
        params.get("cancel_reason")
        or variables.get("cancel_reason")
        or "사용자 요청에 의한 예약 취소"
    )

    if not organization_id:
        return {
            "ok": False,
            "cancelled": False,
            "message": "organization_id가 필요합니다.",
            "error_code": "ORGANIZATION_ID_MISSING",
        }

    if not reservation_id and selected_option_no and options:
        reservation_id = resolve_reservation_id_from_options(
            selected_option_no=selected_option_no,
            options=options,
        )

    if not reservation_id:
        return {
            "ok": False,
            "cancelled": False,
            "message": "취소할 예약을 찾기 위해 reservation_id 또는 선택 번호가 필요합니다.",
            "error_code": "RESERVATION_ID_MISSING",
        }

    supabase = get_supabase_client()

    # 1. 취소 대상 예약 조회
    lookup_response = (
        supabase
        .table("reservations")
        .select(
            "id, organization_id, customer_name, customer_phone, product_name, "
            "scheduled_start_at, scheduled_end_at, status, google_calendar_event_id"
        )
        .eq("id", reservation_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    reservations = lookup_response.data or []

    if not reservations:
        return {
            "ok": False,
            "cancelled": False,
            "reservation_id": reservation_id,
            "message": "취소할 예약을 찾지 못했습니다.",
            "error_code": "RESERVATION_NOT_FOUND",
        }

    reservation = reservations[0]

    if reservation.get("status") == "cancelled":
        return {
            "ok": True,
            "cancelled": True,
            "already_cancelled": True,
            "reservation": reservation,
            "message": "이미 취소된 예약입니다.",
        }

    if reservation.get("status") not in ["reserved", "confirmed"]:
        return {
            "ok": False,
            "cancelled": False,
            "reservation": reservation,
            "message": f"현재 상태가 {reservation.get('status')}인 예약은 취소할 수 없습니다.",
            "error_code": "RESERVATION_NOT_CANCELABLE",
        }

    now = datetime.now(timezone.utc).isoformat()

    # 2. 예약 취소 처리
    update_response = (
        supabase
        .table("reservations")
        .update(
            {
                "status": "cancelled",
                "cancel_reason": cancel_reason,
                "cancelled_at": now,
                "updated_at": now,
            }
        )
        .eq("id", reservation_id)
        .eq("organization_id", organization_id)
        .execute()
    )

    updated_reservations = update_response.data or []

    if not updated_reservations:
        return {
            "ok": False,
            "cancelled": False,
            "reservation_id": reservation_id,
            "message": "예약 취소 처리에 실패했습니다.",
            "error_code": "RESERVATION_CANCEL_FAILED",
        }

    cancelled_reservation = updated_reservations[0]

    return {
        "ok": True,
        "cancelled": True,
        "already_cancelled": False,
        "reservation_id": reservation_id,
        "reservation": cancelled_reservation,
        "message": (
            f"{cancelled_reservation.get('product_name')} 예약이 취소되었습니다."
        ),
    }


def resolve_reservation_id_from_options(
    selected_option_no: Any,
    options: list[dict[str, Any]],
) -> str | None:
    """
    사용자가 '2번'처럼 선택했을 때 options에서 reservation_id를 찾는다.
    """

    try:
        option_no = int(str(selected_option_no).replace("번", "").strip())
    except ValueError:
        return None

    for option in options:
        if option.get("option_no") == option_no:
            return option.get("reservation_id")

    return None



def build_cancelable_reservation_message(
    options: list[dict[str, Any]],
) -> str:
    if not options:
        return "취소 가능한 예약을 찾지 못했습니다."

    if len(options) == 1:
        option = options[0]
        return (
            "취소 가능한 예약이 1건 있습니다.\n"
            f"1. {option['product_name']} - {option['scheduled_start_at']}\n"
            "이 예약을 취소할까요?"
        )

    lines = ["취소 가능한 예약이 여러 건 있습니다. 어떤 예약을 취소하시겠어요?"]

    for option in options:
        lines.append(
            f"{option['option_no']}. "
            f"{option['product_name']} - "
            f"{option['scheduled_start_at']}"
        )

    return "\n".join(lines)


FUNCTION_REGISTRY: dict[str, RegisteredTaskFunction] = {
    "check_required_variables": RegisteredTaskFunction(
        name="check_required_variables",
        handler=check_required_variables,
        description="memory에 필수 값이 모두 있는지 확인한다.",
    ),
    "check_reservation_availability": RegisteredTaskFunction(
        name="check_reservation_availability",
        handler=check_reservation_availability,
        description="예약 가능 여부를 확인한다.",
    ),
    "create_reservation_request": RegisteredTaskFunction(
        name="create_reservation_request",
        handler=create_reservation_request,
        description="예약 요청 정보를 생성한다.",
    ),
    "lookup_cancelable_reservations": RegisteredTaskFunction(
        name="lookup_cancelable_reservations",
        handler=lookup_cancelable_reservations,
        description="취소 가능한 예약 목록을 조회한다.",
    ),
    "cancel_reservation": RegisteredTaskFunction(
        name="cancel_reservation",
        handler=cancel_reservation,
        description="예약을 취소 상태로 변경한다.",
    ),
    "reservation.list_services": RegisteredTaskFunction(
        name="reservation.list_services",
        handler=reservation_list_services,
        description="예약 가능한 서비스 목록을 조회한다.",
    ),

    "reservation.get_available_slots": RegisteredTaskFunction(
        name="reservation.get_available_slots",
        handler=reservation_get_available_slots,
        description="서비스와 날짜 기준으로 예약 가능한 시간을 조회한다.",
    ),

    "reservation.create_reservation": RegisteredTaskFunction(
        name="reservation.create_reservation",
        handler=reservation_create_reservation,
        description="수집된 고객/서비스/시간 정보로 예약 요청을 생성한다.",
    ),
}


def get_task_function(function_name: str) -> RegisteredTaskFunction | None:
    return FUNCTION_REGISTRY.get(function_name)