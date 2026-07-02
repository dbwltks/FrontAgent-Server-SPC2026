"""
예약 관련 툴 핸들러.

DynamicTaskRunner/노드 플로우 없이 function_registry 함수를 직접 호출한다.
LLM(agent_node)이 대화 히스토리를 보고 슬롯을 수집하고 툴 호출 타이밍을 판단한다.
"""
import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.tasks.function_registry import (
    reservation_list_services,
    reservation_resolve_service_item,
    reservation_create_reservation,
    reservation_list_reservations,
    reservation_cancel_reservation,
    reservation_get_available_slots,
)

logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class CreateReservationArgs(BaseModel):
    service_item_name: str = Field(description="예약할 서비스 항목명. 예: 이사 청소, 베란다 청소")
    customer_name: str = Field(description="예약자 성함")
    reservation_date: str = Field(description="예약 날짜 YYYY-MM-DD")
    reservation_time: str = Field(description="예약 시간 HH:MM (24시간)")
    customer_phone: str = Field(description="연락처. 예: 010-1234-5678")


class LookupReservationArgs(BaseModel):
    customer_phone: str = Field(description="예약 시 사용한 연락처")


class CancelReservationArgs(BaseModel):
    reservation_id: str = Field(description="취소할 예약 ID")


class ListServicesArgs(BaseModel):
    pass


# ── 툴 실행 함수 ─────────────────────────────────────────────────────────────

def _vars(organization_id: str, **extra) -> dict[str, Any]:
    return {"organization_id": organization_id, **extra}


async def handle_list_services(organization_id: str) -> str:
    result = await asyncio.to_thread(
        reservation_list_services,
        params={"organization_id": organization_id},
        variables={},
    )
    services = result.get("services") or []
    if not services:
        return "현재 예약 가능한 서비스가 없습니다."

    names = [s.get("name") or s.get("item_name") or "" for s in services if s.get("name") or s.get("item_name")]
    return f"예약 가능한 서비스: {', '.join(names)}"


async def handle_create_reservation(organization_id: str, session_id: str, args: dict) -> tuple[str, bool]:
    """
    (answer, should_end_session) 반환.
    """
    service_item_name = args.get("service_item_name", "")
    customer_name = args.get("customer_name", "")
    reservation_date = args.get("reservation_date", "")
    reservation_time = args.get("reservation_time", "")
    customer_phone = args.get("customer_phone", "")

    # 서비스 항목 해석
    resolve = await asyncio.to_thread(
        reservation_resolve_service_item,
        params={"organization_id": organization_id, "service_item_text": service_item_name},
        variables={},
    )
    if not resolve.get("ok"):
        available = resolve.get("services") or []
        if available:
            names = [s.get("name") or s.get("item_name") or "" for s in available]
            return f"'{service_item_name}'을 찾지 못했습니다. {', '.join(names)} 중에서 선택해 주세요.", False
        return f"'{service_item_name}' 서비스를 찾지 못했습니다. 서비스명을 다시 말씀해 주세요.", False

    service_item_id = resolve.get("service_item_id") or resolve.get("service_item", {}).get("id")
    service_item = resolve.get("service_item") or {}
    service_id = service_item.get("service_id")

    # 가용 슬롯 확인
    avail = await asyncio.to_thread(
        reservation_get_available_slots,
        params={
            "organization_id": organization_id,
            "service_item_id": service_item_id,
            "date": reservation_date,
            "time": reservation_time,
        },
        variables={},
    )
    if not avail.get("is_available"):
        return (
            f"{reservation_date} {reservation_time}에는 예약이 어렵습니다. "
            "다른 날짜나 시간을 알려주시겠어요?",
            False,
        )

    # 예약 생성
    result = await asyncio.to_thread(
        reservation_create_reservation,
        params={
            "organization_id": organization_id,
            "service_item_id": service_item_id,
            "service_id": service_id,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "date": reservation_date,
            "time": reservation_time,
            "source_channel": "web_call",
        },
        variables={},
    )

    if not result.get("ok"):
        err = result.get("message") or result.get("error_code") or ""
        if "duration" in err.lower() or "duration_minutes" in err.lower():
            return "예약 처리 중 오류가 발생했습니다. 담당자 확인 후 연락드리겠습니다.", False
        return f"예약 처리 중 문제가 발생했습니다: {err} 다시 시도해 주세요.", False

    return (
        f"{customer_name}님, 예약이 완료되었습니다. "
        f"{reservation_date} {reservation_time}에 뵙겠습니다. "
        "추가로 궁금한 점 있으신가요?",
        False,
    )


async def handle_lookup_reservation(organization_id: str, args: dict) -> str:
    customer_phone = args.get("customer_phone", "")
    result = await asyncio.to_thread(
        reservation_list_reservations,
        params={"organization_id": organization_id, "customer_phone": customer_phone},
        variables={},
    )
    reservations = result.get("reservations") or []
    if not reservations:
        return f"{customer_phone}로 등록된 예약을 찾지 못했습니다."

    lines = []
    for r in reservations[:3]:
        lines.append(
            f"- {r.get('service_name') or r.get('service_item_name') or '서비스'} "
            f"{r.get('start_at') or r.get('reservation_date') or ''} "
            f"(상태: {r.get('status') or '확인 중'})"
        )
    return "예약 내역입니다:\n" + "\n".join(lines)


async def handle_cancel_reservation(organization_id: str, args: dict) -> str:
    reservation_id = args.get("reservation_id", "")
    result = await asyncio.to_thread(
        reservation_cancel_reservation,
        params={"organization_id": organization_id, "reservation_id": reservation_id},
        variables={},
    )
    if result.get("cancelled"):
        return "예약이 취소되었습니다."
    err = result.get("message") or result.get("error_code") or ""
    return f"예약 취소 중 문제가 발생했습니다. {err}"
