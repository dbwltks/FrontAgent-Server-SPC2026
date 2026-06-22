from dataclasses import dataclass
from typing import Any, Callable


TaskFunctionHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass
class RegisteredTaskFunction:
    name: str
    handler: TaskFunctionHandler
    description: str = ""


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
}


def get_task_function(function_name: str) -> RegisteredTaskFunction | None:
    return FUNCTION_REGISTRY.get(function_name)