from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo
from supabase import create_client
from app.core.config import settings
import re
import calendar

from app.repositories.service_repo import (
    get_service_item as repo_get_service_item,
    list_service_items,
    resolve_service_item_by_name,
    resolve_service_options_by_name,
)

from app.repositories.reservation_repo import (
    NotFoundError,
    ReservationConflictError,
    ReservationRepoError,
    cancel_reservation as repo_cancel_reservation,
    create_reservation as repo_create_reservation,
    get_available_slots as repo_get_available_slots,
    get_reservation as repo_get_reservation,
    get_service as repo_get_service,
    list_reservations as repo_list_reservations,
    list_services as repo_list_services,
)

from app.repositories.product_repo import (
    ProductNotFoundError,
    ProductRepoError,
    ProductStockError,
    check_product_stock as repo_check_product_stock,
    get_product as repo_get_product,
    list_products as repo_list_products,
    search_products as repo_search_products,
)

from app.repositories.order_repo import (
    OrderNotFoundError,
    OrderRepoError,
    OrderStatusError,
    cancel_order as repo_cancel_order,
    confirm_order as repo_confirm_order,
    create_order as repo_create_order,
    lookup_orders_by_phone as repo_lookup_orders_by_phone,
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
   
def _read_path(source: dict[str, Any], key: str) -> Any:
    """
    dict에서 key 또는 dot path 값을 읽는다.
    예:
    - customer_phone
    - lookup_result.reservations
    """
    if not isinstance(source, dict):
        return None

    if "." not in key:
        return source.get(key)

    current: Any = source

    for part in key.split("."):
        if not isinstance(current, dict):
            return None

        current = current.get(part)

        if current is None:
            return None

    return current


def _get_value(
    params: dict[str, Any],
    variables: dict[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    """
    Function Node params 값을 우선 사용하고,
    없으면 task memory variables에서 찾는다.
    dot path도 지원한다.
    """
    for key in keys:
        value = _read_path(params, key)
        if value is not None and value != "":
            return value

        value = _read_path(variables, key)
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

def _product_order_error_result(error: Exception) -> dict[str, Any]:
    if isinstance(error, ProductNotFoundError):
        return {
            "ok": False,
            "error_code": "product_not_found",
            "message": str(error),
        }

    if isinstance(error, ProductStockError):
        return {
            "ok": False,
            "error_code": "product_stock_error",
            "message": str(error),
        }

    if isinstance(error, ProductRepoError):
        return {
            "ok": False,
            "error_code": "product_repo_error",
            "message": str(error),
        }

    if isinstance(error, OrderNotFoundError):
        return {
            "ok": False,
            "error_code": "order_not_found",
            "message": str(error),
        }

    if isinstance(error, OrderStatusError):
        return {
            "ok": False,
            "error_code": "order_status_error",
            "message": str(error),
        }

    if isinstance(error, OrderRepoError):
        return {
            "ok": False,
            "error_code": "order_repo_error",
            "message": str(error),
        }

    return {
        "ok": False,
        "error_code": "unknown_error",
        "message": str(error),
    }



def _normalize_status_filter(value: Any) -> list[str] | None:
    if value is None or value == "":
        return None

    if isinstance(value, str):
        return [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]

    if isinstance(value, (list, tuple, set)):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    return [str(value).strip()]


def _parse_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None

    try:
        parsed = int(str(value).replace("번", "").strip())
    except ValueError:
        return None

    if parsed <= 0:
        return None

    return parsed


def _format_reservation_option(
    reservation: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    ordered_summary = reservation.get("ordered_summary") or {}
    service_item = ordered_summary.get("service_item") or {}
    service_name = service_item.get("name") or reservation.get("service_name") or "서비스"

    start_at = reservation.get("start_at")
    start_label = start_at
    if start_at:
        try:
            start_label = _parse_datetime_value(start_at, "Asia/Seoul").strftime(
                "%m/%d %H:%M"
            )
        except Exception:
            start_label = start_at

    customer_name = reservation.get("customer_name") or "고객"
    status = reservation.get("status") or "-"

    return {
        "number": index,
        "reservation_id": reservation.get("id"),
        "customer_name": reservation.get("customer_name"),
        "customer_phone": reservation.get("customer_phone"),
        "service_id": reservation.get("service_id"),
        "start_at": reservation.get("start_at"),
        "end_at": reservation.get("end_at"),
        "status": reservation.get("status"),
        "label": f"{index}. {service_name} / {start_label} / {customer_name} ({status})",
    }


def _resolve_reservation_id_from_memory(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> str | None:
    reservation_id = _get_value(
        params,
        variables,
        "reservation_id",
        "selected_reservation_id",
    )

    if reservation_id:
        return str(reservation_id)

    selected_number = _get_value(
        params,
        variables,
        "selected_reservation_number",
        "reservation_number",
        "selected_number",
        "selected_option_no",
        "option_no",
    )

    parsed_number = _parse_positive_int(selected_number)

    if not parsed_number:
        return None

    reservations = _get_value(
        params,
        variables,
        "cancelable_reservations",
        "reservations",
        "lookup_result.cancelable_reservations",
        "lookup_result.reservations",
        "reservation_lookup_result.cancelable_reservations",
        "reservation_lookup_result.reservations",
    )

    if not isinstance(reservations, list):
        return None

    index = parsed_number - 1

    if index < 0 or index >= len(reservations):
        return None

    selected = reservations[index]

    if not isinstance(selected, dict):
        return None

    return selected.get("id")

def _format_product_option(
    product: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    return {
        "number": index,
        "product_id": product.get("id"),
        "name": product.get("name"),
        "category": product.get("category"),
        "price": product.get("price"),
        "stock_quantity": product.get("stock_quantity"),
        "label": (
            f"{index}. {product.get('name')} / "
            f"{product.get('category') or '카테고리 없음'} / "
            f"{product.get('price') or 0}원 / "
            f"재고 {product.get('stock_quantity') or 0}개"
        ),
    }


def _resolve_product_id_from_memory(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> str | None:
    product_id = _get_value(
        params,
        variables,
        "product_id",
        "selected_product_id",
    )

    if product_id:
        return str(product_id)

    selected_number = _get_value(
        params,
        variables,
        "selected_product_number",
        "product_number",
        "selected_number",
        "selected_option_no",
        "option_no",
    )

    parsed_number = _parse_positive_int(selected_number)

    if not parsed_number:
        return None

    products = _get_value(
        params,
        variables,
        "products",
        "product_options",
        "search_result.products",
        "search_result.product_options",
        "product_search_result.products",
        "product_search_result.product_options",
        "product.list_products_result.products",
        "product.search_products_result.products",
    )

    if not isinstance(products, list):
        return None

    index = parsed_number - 1

    if index < 0 or index >= len(products):
        return None

    selected = products[index]

    if not isinstance(selected, dict):
        return None

    return selected.get("product_id") or selected.get("id")


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

    고객이 실제로 골라야 하는 단위는 services(대분류, 예: "홈 클리닝")가
    아니라 service_items(세부 예약 항목, 예: "이사 청소")다. resolve_service_
    item도 service_items를 대상으로 이름을 매칭하므로, 여기서 대분류만
    보여주면 고객 답변이 항상 매칭 실패로 되돌아간다(실측된 회귀). 세부
    항목이 하나도 등록되지 않은 조직(과도기)만 대분류로 폴백한다.
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
        services = list_service_items(organization_id=organization_id)

        if not services:
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
    service_item_id = _get_value(params, variables, "service_item_id")
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

    # 서비스 아이템(service_items) 단위로 예약을 받는 플로우는 memory에
    # service_id 없이 service_item_id만 있다(resolve_service_item이
    # 채워준다). 슬롯 조회는 대분류(services) 단위라 service_item이
    # 속한 service_id를 역으로 찾아 채운다.
    if not service_id and service_item_id and organization_id:
        service_item = repo_get_service_item(organization_id=organization_id, service_item_id=service_item_id)
        if service_item:
            service_id = service_item.get("service_id")

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
            service_item_id=service_item_id,
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
    

def _normalize_string_list(value: Any) -> list[str]:
    """
    Function Node params/memory에서 넘어온 값을 문자열 리스트로 정규화한다.

    허용 형태:
    - ["uuid1", "uuid2"]
    - "uuid1"
    - "uuid1,uuid2"
    - [{"id": "uuid1"}, {"id": "uuid2"}]
    """
    if value is None:
        return []

    if isinstance(value, list):
        normalized = []

        for item in value:
            if isinstance(item, dict):
                item_id = item.get("id")
                if item_id:
                    normalized.append(str(item_id))
                continue

            if item:
                normalized.append(str(item))

        return normalized

    if isinstance(value, str):
        value = value.strip()

        if value in {"", "없음", "없어요", "없어", "no", "none", "null", "[]"}:
            return []

        if "," in value:
            return [
                part.strip()
                for part in value.split(",")
                if part.strip()
                and part.strip() not in {"없음", "없어요", "없어", "no", "none", "null", "[]"}
            ]

        return [value]

    return [str(value)]

def reservation_resolve_service_item(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    service_id = _get_value(params, variables, "service_id")

    service_item_text = _get_value(
        params,
        variables,
        "service_item_text",
        "service_item_name",
        "service_item_input",
        "service_item",
    )

    if not organization_id:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "organization_id_missing",
            "message": "organization_id가 필요합니다.",
            "service_item_id": None,
        }

    if not service_item_text:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "service_item_text_missing",
            "message": "예약할 서비스명을 입력해주세요.",
            "service_item_id": None,
        }

    return resolve_service_item_by_name(
        organization_id=organization_id,
        service_id=service_id,
        user_text=str(service_item_text),
    )


def reservation_resolve_service_options(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    service_item_id = _get_value(params, variables, "service_item_id")

    option_text = _get_value(
        params,
        variables,
        "option_text",
        "option_name",
        "option_input",
        "selected_options_text",
        "selected_option_names",
        "selected_option_ids",
        default="",
    )

    if not organization_id:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "organization_id_missing",
            "message": "organization_id가 필요합니다.",
            "selected_option_ids": [],
        }

    if not service_item_id:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "service_item_id_missing",
            "message": "서비스 아이템을 먼저 선택해야 합니다.",
            "selected_option_ids": [],
        }

    return resolve_service_options_by_name(
        organization_id=organization_id,
        service_item_id=service_item_id,
        user_text=str(option_text or ""),
    )

def _add_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _parse_korean_reservation_datetime(
    text: str,
    timezone: str = "Asia/Seoul",
) -> datetime | None:
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    raw_text = str(text or "").strip()

    if not raw_text:
        return None

    # 이미 ISO 형식으로 들어온 경우도 그대로 허용
    try:
        parsed = datetime.fromisoformat(raw_text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    except ValueError:
        pass

    normalized = raw_text.replace(" ", "")

    # 날짜 파싱
    target_date = None

    if "오늘" in normalized:
        target_date = now.date()
    elif "내일" in normalized:
        target_date = (now + timedelta(days=1)).date()
    elif "모레" in normalized:
        target_date = (now + timedelta(days=2)).date()
    elif "글피" in normalized:
        target_date = (now + timedelta(days=3)).date()

    # 예: 7월5일, 7/5
    if target_date is None:
        month_day_match = re.search(r"(?P<month>\d{1,2})\s*(월|/)\s*(?P<day>\d{1,2})\s*일?", raw_text)
        if month_day_match:
            month = int(month_day_match.group("month"))
            day = int(month_day_match.group("day"))
            year = now.year

            candidate = datetime(year, month, day, tzinfo=tz).date()
            if candidate < now.date():
                year += 1

            target_date = datetime(year, month, day, tzinfo=tz).date()

    # 예: 5일 오후 1시
    if target_date is None:
        day_match = re.search(r"(?P<day>\d{1,2})\s*일", raw_text)
        if day_match:
            day = int(day_match.group("day"))
            year = now.year
            month = now.month

            last_day = calendar.monthrange(year, month)[1]

            if day > last_day:
                return None

            candidate = datetime(year, month, day, tzinfo=tz).date()

            if candidate < now.date():
                year, month = _add_month(year, month)
                last_day = calendar.monthrange(year, month)[1]

                if day > last_day:
                    return None

                candidate = datetime(year, month, day, tzinfo=tz).date()

            target_date = candidate

    if target_date is None:
        return None

    # 시간 파싱
    time_match = re.search(
        r"(?P<ampm>오전|오후|아침|낮|저녁|밤)?\s*(?P<hour>\d{1,2})\s*시\s*(?P<minute>\d{1,2})?\s*분?",
        raw_text,
    )

    if not time_match:
        return None

    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute") or 0)
    ampm = time_match.group("ampm")

    if hour > 23 or minute > 59:
        return None

    if ampm in {"오후", "낮", "저녁", "밤"}:
        if hour < 12:
            hour += 12
    elif ampm == "오전":
        if hour == 12:
            hour = 0
    else:
        # 예약 서비스에서는 "3시"를 보통 오후 3시로 말하는 경우가 많아서
        # 1~7시는 오후로 보정
        if 1 <= hour <= 7:
            hour += 12

    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        minute,
        tzinfo=tz,
    )


def reservation_normalize_start_at(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    start_at_text = _get_value(
        params,
        variables,
        "start_at_text",
        "start_at",
        "reservation_time_text",
        "datetime_text",
    )

    timezone = _get_value(
        params,
        variables,
        "timezone",
        default="Asia/Seoul",
    )

    if not start_at_text:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "start_at_text_missing",
            "message": "예약 날짜와 시간을 입력해주세요.",
            "start_at": None,
        }

    parsed = _parse_korean_reservation_datetime(
        text=str(start_at_text),
        timezone=str(timezone or "Asia/Seoul"),
    )

    if parsed is None:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "start_at_parse_failed",
            "message": "예약 날짜와 시간을 이해하지 못했습니다. 예: 내일 오후 3시, 5일 오후 1시",
            "start_at": None,
            "start_at_text": start_at_text,
        }

    return {
        "ok": True,
        "resolved": True,
        "start_at": parsed.isoformat(),
        "start_at_text": start_at_text,
        "timezone": str(timezone or "Asia/Seoul"),
        "message": None,
    }

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

    service_item_id = _get_value(params, variables, "service_item_id")

    # service_id가 직접 안 넘어오고 service_item_id만 있는 경우(현재 예약 플로우는
    # 대분류 service가 아니라 세부 service_item 단위로 선택을 받음) service_item에서
    # 역추론한다. service_item이 어느 service에 속하는지는 service_id 컬럼에 있다.
    if not service_id and service_item_id and organization_id:
        service_item_for_id = repo_get_service_item(
            organization_id=organization_id, service_item_id=service_item_id
        )
        if service_item_for_id:
            service_id = service_item_for_id.get("service_id")
    selected_option_ids = _normalize_string_list(
        _get_value(
            params,
            variables,
            "selected_option_ids",
            "option_ids",
            "service_option_ids",
            default=[],
        )
    )

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
    if selected_option_ids and not service_item_id:
        missing_keys.append("service_item_id")
    if not customer_name:
        missing_keys.append("customer_name")
    if not customer_phone:
        missing_keys.append("customer_phone")
    if not start_at:
        missing_keys.append("start_at")
    if not end_at and not service_item_id:
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
            service_item_id=service_item_id,
            selected_option_ids=selected_option_ids,
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

def reservation_list_reservations(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    customer_phone = _get_value(params, variables, "customer_phone", "phone")
    status_value = _get_value(params, variables, "status")
    limit_value = _get_value(params, variables, "limit", default=10)

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not customer_phone:
        missing_keys.append("customer_phone")

    if missing_keys:
        return {
            "ok": False,
            "found": False,
            "has_reservations": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "reservations": [],
            "reservation_options": [],
            "count": 0,
        }

    try:
        status_filter = _normalize_status_filter(status_value)
        limit = _parse_positive_int(limit_value) or 10

        if status_filter and len(status_filter) == 1:
            reservations = repo_list_reservations(
                organization_id=organization_id,
                customer_phone=customer_phone,
                status=status_filter[0],
                limit=limit,
            )
        else:
            reservations = repo_list_reservations(
                organization_id=organization_id,
                customer_phone=customer_phone,
                limit=limit,
            )

        if status_filter and len(status_filter) > 1:
            reservations = [
                reservation
                for reservation in reservations
                if reservation.get("status") in status_filter
            ]

        reservation_options = [
            _format_reservation_option(reservation, index)
            for index, reservation in enumerate(reservations, start=1)
        ]

        variables_for_message = {
            "reservation_options": reservation_options,
        }
        from app.tasks.service_selection import build_lookup_result_message

        lookup_result_message = build_lookup_result_message(variables_for_message)

        return {
            "ok": True,
            "found": len(reservations) > 0,
            "has_reservations": len(reservations) > 0,
            "customer_phone": customer_phone,
            "reservations": reservations,
            "reservation_options": reservation_options,
            "lookup_result_message": lookup_result_message,
            "count": len(reservations),
        }

    except Exception as error:
        result = _domain_error_result(error)
        result.update(
            {
                "found": False,
                "has_reservations": False,
                "reservations": [],
                "reservation_options": [],
                "count": 0,
            }
        )
        return result


def reservation_lookup_cancelable_reservations(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    params_with_status = {
        **params,
        "status": params.get("status") or ["requested", "confirmed"],
    }

    result = reservation_list_reservations(
        params=params_with_status,
        variables=variables,
    )

    reservations = result.get("reservations") or []

    cancelable_reservations = [
        reservation
        for reservation in reservations
        if reservation.get("status") in ["requested", "confirmed"]
    ]

    cancelable_options = [
        _format_reservation_option(reservation, index)
        for index, reservation in enumerate(cancelable_reservations, start=1)
    ]

    result.update(
        {
            "cancelable_reservations": cancelable_reservations,
            "cancelable_options": cancelable_options,
            "has_cancelable_reservations": len(cancelable_reservations) > 0,
            "cancelable_count": len(cancelable_reservations),
        }
    )

    return result


def reservation_get_reservation(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    reservation_id = _resolve_reservation_id_from_memory(params, variables)

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not reservation_id:
        missing_keys.append("reservation_id")

    if missing_keys:
        return {
            "ok": False,
            "found": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "reservation": None,
        }

    try:
        reservation = repo_get_reservation(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        return {
            "ok": True,
            "found": True,
            "reservation_id": reservation.get("id"),
            "reservation": reservation,
            "status": reservation.get("status"),
        }

    except Exception as error:
        result = _domain_error_result(error)
        result.update(
            {
                "found": False,
                "reservation": None,
            }
        )
        return result


def reservation_cancel_reservation(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    reservation_id = _resolve_reservation_id_from_memory(params, variables)

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not reservation_id:
        missing_keys.append("reservation_id")

    if missing_keys:
        return {
            "ok": False,
            "cancelled": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "reservation_id": None,
            "reservation": None,
        }

    try:
        reservation = repo_cancel_reservation(
            organization_id=organization_id,
            reservation_id=reservation_id,
        )

        return {
            "ok": True,
            "cancelled": True,
            "reservation_id": reservation.get("id"),
            "status": reservation.get("status"),
            "reservation": reservation,
            "message": "예약이 취소되었습니다.",
        }

    except Exception as error:
        result = _domain_error_result(error)
        result.update(
            {
                "cancelled": False,
                "reservation_id": reservation_id,
                "reservation": None,
            }
        )
        return result

def product_list_products(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    category = _get_value(params, variables, "category")
    limit_value = _get_value(params, variables, "limit", default=20)
    limit = _parse_positive_int(limit_value) or 20

    if not organization_id:
        return {
            "ok": False,
            "error_code": "organization_id_missing",
            "message": "organization_id가 필요합니다.",
            "products": [],
            "product_options": [],
            "count": 0,
        }

    try:
        products = repo_list_products(
            organization_id=organization_id,
            category=category,
            limit=limit,
        )

        product_options = [
            _format_product_option(product, index)
            for index, product in enumerate(products, start=1)
        ]

        return {
            "ok": True,
            "products": products,
            "product_options": product_options,
            "has_products": len(products) > 0,
            "count": len(products),
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "products": [],
                "product_options": [],
                "has_products": False,
                "count": 0,
            }
        )
        return result


def product_search_products(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    keyword = _get_value(
        params,
        variables,
        "keyword",
        "product_name",
        "category",
        "user_message",
    )
    category = _get_value(params, variables, "category")
    limit_value = _get_value(params, variables, "limit", default=20)
    limit = _parse_positive_int(limit_value) or 20

    if not organization_id:
        return {
            "ok": False,
            "error_code": "organization_id_missing",
            "message": "organization_id가 필요합니다.",
            "products": [],
            "product_options": [],
            "count": 0,
        }

    try:
        products = repo_search_products(
            organization_id=organization_id,
            keyword=keyword,
            category=category,
            limit=limit,
        )

        product_options = [
            _format_product_option(product, index)
            for index, product in enumerate(products, start=1)
        ]

        return {
            "ok": True,
            "keyword": keyword,
            "category": category,
            "products": products,
            "product_options": product_options,
            "has_products": len(products) > 0,
            "count": len(products),
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "products": [],
                "product_options": [],
                "has_products": False,
                "count": 0,
            }
        )
        return result


def product_get_product_detail(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    product_id = _resolve_product_id_from_memory(params, variables)

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not product_id:
        missing_keys.append("product_id")

    if missing_keys:
        return {
            "ok": False,
            "found": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "product": None,
        }

    try:
        product = repo_get_product(
            organization_id=organization_id,
            product_id=product_id,
        )

        if not product:
            return {
                "ok": False,
                "found": False,
                "error_code": "product_not_found",
                "product_id": product_id,
                "product": None,
            }

        return {
            "ok": True,
            "found": True,
            "product_id": product.get("id"),
            "product": product,
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "found": False,
                "product": None,
            }
        )
        return result


def product_check_stock(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    product_id = _resolve_product_id_from_memory(params, variables)
    quantity_value = _get_value(params, variables, "quantity", default=1)
    quantity = _parse_positive_int(quantity_value) or 1

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not product_id:
        missing_keys.append("product_id")

    if missing_keys:
        return {
            "ok": False,
            "available": False,
            "is_available": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "product_id": None,
            "requested_quantity": quantity,
        }

    try:
        stock_result = repo_check_product_stock(
            organization_id=organization_id,
            product_id=product_id,
            quantity=quantity,
        )

        return {
            "ok": True,
            "available": stock_result.get("available"),
            "is_available": stock_result.get("available"),
            **stock_result,
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "available": False,
                "is_available": False,
                "product_id": product_id,
                "requested_quantity": quantity,
            }
        )
        return result

def order_create_order(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    customer_name = _get_value(params, variables, "customer_name", "name")
    customer_phone = _get_value(params, variables, "customer_phone", "phone")
    customer_email = _get_value(params, variables, "customer_email", "email")

    recipient_name = _get_value(params, variables, "recipient_name")
    recipient_phone = _get_value(params, variables, "recipient_phone")
    postal_code = _get_value(params, variables, "postal_code")
    address_line1 = _get_value(params, variables, "address_line1", "address")
    address_line2 = _get_value(params, variables, "address_line2")
    delivery_memo = _get_value(params, variables, "delivery_memo")
    source_channel = _get_value(params, variables, "source_channel", default="web_chat")
    memo = _get_value(params, variables, "memo")

    items = _get_value(params, variables, "items", "order_items")

    if not isinstance(items, list):
        product_id = _resolve_product_id_from_memory(params, variables)
        quantity_value = _get_value(params, variables, "quantity", default=1)
        quantity = _parse_positive_int(quantity_value) or 1
        selected_options = _get_value(params, variables, "selected_options", default={})

        if product_id:
            items = [
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "selected_options": selected_options or {},
                }
            ]
        else:
            items = []

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not customer_name:
        missing_keys.append("customer_name")

    if not customer_phone:
        missing_keys.append("customer_phone")

    if not items:
        missing_keys.append("items")

    if missing_keys:
        return {
            "ok": False,
            "created": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "order_id": None,
            "order": None,
        }

    try:
        order = repo_create_order(
            organization_id=organization_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            items=items,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            postal_code=postal_code,
            address_line1=address_line1,
            address_line2=address_line2,
            delivery_memo=delivery_memo,
            source_channel=source_channel,
            memo=memo,
        )

        return {
            "ok": True,
            "created": True,
            "order_id": order.get("id"),
            "order_code": order.get("order_code"),
            "order_status": order.get("order_status"),
            "delivery_status": order.get("delivery_status"),
            "total_amount": order.get("total_amount"),
            "order": order,
            "message": "주문 요청이 접수되었습니다.",
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "created": False,
                "order_id": None,
                "order": None,
            }
        )
        return result


def order_lookup_orders(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    customer_phone = _get_value(params, variables, "customer_phone", "phone")
    limit_value = _get_value(params, variables, "limit", default=10)
    limit = _parse_positive_int(limit_value) or 10

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not customer_phone:
        missing_keys.append("customer_phone")

    if missing_keys:
        return {
            "ok": False,
            "found": False,
            "has_orders": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "orders": [],
            "count": 0,
        }

    try:
        orders = repo_lookup_orders_by_phone(
            organization_id=organization_id,
            customer_phone=customer_phone,
            limit=limit,
        )

        return {
            "ok": True,
            "found": len(orders) > 0,
            "has_orders": len(orders) > 0,
            "customer_phone": customer_phone,
            "orders": orders,
            "count": len(orders),
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "found": False,
                "has_orders": False,
                "orders": [],
                "count": 0,
            }
        )
        return result


def order_confirm_order(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    order_id = _get_value(params, variables, "order_id", "selected_order_id")

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not order_id:
        missing_keys.append("order_id")

    if missing_keys:
        return {
            "ok": False,
            "confirmed": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "order_id": None,
            "order": None,
        }

    try:
        order = repo_confirm_order(
            organization_id=organization_id,
            order_id=order_id,
        )

        return {
            "ok": True,
            "confirmed": True,
            "order_id": order.get("id"),
            "order_status": order.get("order_status"),
            "delivery_status": order.get("delivery_status"),
            "order": order,
            "message": "주문이 확정되었습니다.",
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "confirmed": False,
                "order_id": order_id,
                "order": None,
            }
        )
        return result


def order_cancel_order(
    params: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    organization_id = _get_value(params, variables, "organization_id")
    order_id = _get_value(params, variables, "order_id", "selected_order_id")

    missing_keys = []

    if not organization_id:
        missing_keys.append("organization_id")

    if not order_id:
        missing_keys.append("order_id")

    if missing_keys:
        return {
            "ok": False,
            "cancelled": False,
            "error_code": "missing_required_fields",
            "missing_keys": missing_keys,
            "order_id": None,
            "order": None,
        }

    try:
        order = repo_cancel_order(
            organization_id=organization_id,
            order_id=order_id,
        )

        return {
            "ok": True,
            "cancelled": True,
            "order_id": order.get("id"),
            "order_status": order.get("order_status"),
            "delivery_status": order.get("delivery_status"),
            "order": order,
            "message": "주문이 취소되었습니다.",
        }

    except Exception as error:
        result = _product_order_error_result(error)
        result.update(
            {
                "cancelled": False,
                "order_id": order_id,
                "order": None,
            }
        )
        return result




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
    "reservation.list_reservations": RegisteredTaskFunction(
        name="reservation.list_reservations",
        handler=reservation_list_reservations,
        description="고객 전화번호 기준으로 예약 목록을 조회한다.",
    ),

    "reservation.lookup_cancelable_reservations": RegisteredTaskFunction(
        name="reservation.lookup_cancelable_reservations",
        handler=reservation_lookup_cancelable_reservations,
        description="고객 전화번호 기준으로 취소 가능한 예약 목록을 조회한다.",
    ),

    "reservation.get_reservation": RegisteredTaskFunction(
        name="reservation.get_reservation",
        handler=reservation_get_reservation,
        description="예약 ID 또는 선택 번호 기준으로 예약 상세를 조회한다.",
    ),

    "reservation.cancel_reservation": RegisteredTaskFunction(
        name="reservation.cancel_reservation",
        handler=reservation_cancel_reservation,
        description="예약 ID 또는 선택 번호 기준으로 예약을 취소한다.",
    ),

    "lookup_cancelable_reservations": RegisteredTaskFunction(
        name="lookup_cancelable_reservations",
        handler=reservation_lookup_cancelable_reservations,
        description="취소 가능한 예약 목록을 조회한다. 기존 플로우 호환용 alias.",
    ),
    "cancel_reservation": RegisteredTaskFunction(
        name="cancel_reservation",
        handler=reservation_cancel_reservation,
        description="예약을 취소한다. 기존 플로우 호환용 alias.",
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
    "reservation.resolve_service_item": RegisteredTaskFunction(
        name="reservation.resolve_service_item",
        handler=reservation_resolve_service_item,
        description="사용자가 입력한 서비스 아이템 이름을 service_item_id로 변환한다.",
    ),
    "reservation.resolve_service_options": RegisteredTaskFunction(
        name="reservation.resolve_service_options",
        handler=reservation_resolve_service_options,
        description="사용자가 입력한 옵션 이름들을 selected_option_ids로 변환한다.",
    ),
    "reservation.normalize_start_at": RegisteredTaskFunction(
        name="reservation.normalize_start_at",
        handler=reservation_normalize_start_at,
        description="사용자가 입력한 자연어 예약 시간을 ISO datetime으로 변환한다.",
    ),

    "reservation.create_reservation": RegisteredTaskFunction(
        name="reservation.create_reservation",
        handler=reservation_create_reservation,
        description="수집된 고객/서비스/시간 정보로 예약 요청을 생성한다.",
    ),
        "product.list_products": RegisteredTaskFunction(
        name="product.list_products",
        handler=product_list_products,
        description="조직의 상품 목록을 조회한다.",
    ),
    "product.search_products": RegisteredTaskFunction(
        name="product.search_products",
        handler=product_search_products,
        description="상품명, 카테고리, 설명 기준으로 상품을 검색한다.",
    ),
    "product.get_product_detail": RegisteredTaskFunction(
        name="product.get_product_detail",
        handler=product_get_product_detail,
        description="상품 ID 또는 선택 번호 기준으로 상품 상세를 조회한다.",
    ),
    "product.check_stock": RegisteredTaskFunction(
        name="product.check_stock",
        handler=product_check_stock,
        description="상품 재고를 확인한다.",
    ),
    "order.create_order": RegisteredTaskFunction(
        name="order.create_order",
        handler=order_create_order,
        description="수집된 고객/상품/배송 정보로 상품 주문 요청을 생성한다.",
    ),
    "order.lookup_orders": RegisteredTaskFunction(
        name="order.lookup_orders",
        handler=order_lookup_orders,
        description="고객 전화번호 기준으로 주문 목록을 조회한다.",
    ),
    "order.confirm_order": RegisteredTaskFunction(
        name="order.confirm_order",
        handler=order_confirm_order,
        description="주문을 확정하고 상품 재고를 차감한다.",
    ),
    "order.cancel_order": RegisteredTaskFunction(
        name="order.cancel_order",
        handler=order_cancel_order,
        description="주문을 취소한다.",
    ),
}


def get_task_function(function_name: str) -> RegisteredTaskFunction | None:
    return FUNCTION_REGISTRY.get(function_name)