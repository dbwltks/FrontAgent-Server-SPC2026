from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from supabase import create_client

from app.core.config import settings

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
}


def get_task_function(function_name: str) -> RegisteredTaskFunction | None:
    return FUNCTION_REGISTRY.get(function_name)