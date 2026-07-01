from datetime import datetime, timezone
from typing import Any

from app.core.db import supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_nullable_int(value: Any) -> int | None:
    """
    서비스 추출 저장용 숫자 변환.

    중요:
    - 지식 문서에서 값이 없으면 null이어야 한다.
    - AI가 빈 값을 0으로 잘못 만든 경우가 많으므로 숫자 0은 null로 방어 처리한다.
    - 실제 0원/무료 상품까지 정확히 구분하려면 추후 is_free 같은 별도 필드를 추가하는 게 안전하다.
    """
    if value is None:
        return None

    original = value

    if isinstance(value, str):
        text = value.strip()

        if not text:
            return None

        if text.lower() in {"null", "none", "unknown", "n/a", "nan"}:
            return None

        if text in {"미정", "없음", "-", "확인 필요", "상담 후 결정", "문의"}:
            return None

        text = (
            text.replace(",", "")
            .replace("원", "")
            .replace("분", "")
            .replace("시간", "")
            .strip()
        )

        if not text:
            return None

        value = text

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    if number == 0:
        if isinstance(original, str) and original.strip() in {"0원", "0분", "무료", "무상"}:
            return 0
        return None

    return number


def _is_missing(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str) and not value.strip():
        return True

    return False


def _normalize_key(value: str) -> str:
    return str(value or "").replace(" ", "").strip().lower()


def _find_service_by_name(
    *,
    organization_id: str,
    name: str,
) -> dict | None:
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("name", name)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def _find_service_item_by_name(
    *,
    organization_id: str,
    service_id: str,
    name: str,
) -> dict | None:
    result = (
        supabase.table("service_items")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("service_id", service_id)
        .eq("name", name)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def _find_service_item_option(
    *,
    organization_id: str,
    service_item_id: str,
    option_group: str,
    option_value: str,
) -> dict | None:
    result = (
        supabase.table("service_item_options")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("service_item_id", service_item_id)
        .eq("option_group", option_group)
        .eq("option_value", option_value)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def _upsert_parent_service(
    *,
    organization_id: str,
    knowledge_source_id: str,
    catalog: dict,
) -> dict:
    """
    services 테이블에는 대분류 서비스를 저장한다.
    예: 청소 서비스, 미용 서비스

    중요:
    - 기존 대분류 서비스가 있으면 새 파일을 넣어도 삭제/교체하지 않는다.
    - 기존 서비스의 승인 상태와 활성 상태는 유지한다.
    """
    service_name = str(catalog.get("service_name") or "").strip()

    if not service_name:
        raise ValueError("service_name is required")

    existing = _find_service_by_name(
        organization_id=organization_id,
        name=service_name,
    )

    raw_payload = {
        "latest_source_id": knowledge_source_id,
        "catalog": catalog,
    }

    if existing:
        update_payload = {
            "description": catalog.get("description") or existing.get("description"),
            "raw_payload": raw_payload,
            "sync_status": "synced",
        }

        result = (
            supabase.table("services")
            .update(update_payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        return rows[0] if rows else existing

    insert_payload = {
        "organization_id": organization_id,
        "name": service_name,
        "description": catalog.get("description"),
        "price": None,
        "currency": "KRW",
        "duration_minutes": None,
        "is_reservable": True,
        "is_active": False,
        "approval_status": "pending",
        "source_type": "knowledge",
        "source_id": knowledge_source_id,
        "confidence": None,
        "raw_payload": raw_payload,
        "pending_payload": None,
        "sync_status": "synced",
    }

    result = supabase.table("services").insert(insert_payload).execute()
    rows = result.data or []

    if not rows:
        raise RuntimeError("Failed to create parent service")

    return rows[0]


def _build_item_payload(
    *,
    organization_id: str,
    service_id: str,
    knowledge_source_id: str,
    item: dict,
) -> dict:
    item_name = str(item.get("name") or "").strip()

    if not item_name:
        raise ValueError("service item name is required")

    return {
        "organization_id": organization_id,
        "service_id": service_id,
        "source_id": knowledge_source_id,
        "name": item_name,
        "description": item.get("description"),
        "base_price": _to_nullable_int(item.get("base_price")),
        "duration_minutes": _to_nullable_int(item.get("duration_minutes")),
        "raw_payload": item,
    }


def _build_pending_payload(
    *,
    source_id: str,
    raw_payload: dict,
    suggested: dict,
) -> dict:
    return {
        "source_id": source_id,
        "suggested": suggested,
        "changed_fields": list(suggested.keys()),
        "raw_payload": raw_payload,
        "created_at": _now_iso(),
    }


def _upsert_service_item(
    *,
    organization_id: str,
    service_id: str,
    knowledge_source_id: str,
    item: dict,
) -> dict:
    """
    service_items 테이블에는 실제 예약 가능한 상품을 저장한다.
    예: 이사 청소, 화장실 청소, 베란다 청소

    정책:
    - 새 상품이면 pending + is_available=false 로 추가한다.
    - 기존 상품이 아직 pending이면 AI 추출값으로 계속 갱신한다.
    - 기존 상품이 approved/is_available=true면 확정값을 바로 덮어쓰지 않는다.
    - approved 상품의 가격/시간/설명이 바뀌면 pending_payload에 변경 후보로 저장한다.
    """
    payload = _build_item_payload(
        organization_id=organization_id,
        service_id=service_id,
        knowledge_source_id=knowledge_source_id,
        item=item,
    )

    existing = _find_service_item_by_name(
        organization_id=organization_id,
        service_id=service_id,
        name=payload["name"],
    )

    if not existing:
        insert_payload = {
            **payload,
            "is_available": False,
            "approval_status": "pending",
            "sync_status": "synced",
            "pending_payload": None,
        }

        result = supabase.table("service_items").insert(insert_payload).execute()
        rows = result.data or []

        if not rows:
            raise RuntimeError("Failed to create service item")

        return rows[0]

    is_approved = (
        existing.get("approval_status") == "approved"
        or bool(existing.get("is_available"))
    )

    if not is_approved:
        update_payload = {
            **payload,
            "is_available": False,
            "approval_status": existing.get("approval_status") or "pending",
            "sync_status": "synced",
            "pending_payload": None,
        }

        result = (
            supabase.table("service_items")
            .update(update_payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        return rows[0] if rows else existing

    update_payload: dict[str, Any] = {
        "source_id": knowledge_source_id,
        "raw_payload": item,
    }

    suggested: dict[str, Any] = {}

    for field in ["description", "base_price", "duration_minutes"]:
        current_value = existing.get(field)
        new_value = payload.get(field)

        if _is_missing(current_value) and not _is_missing(new_value):
            update_payload[field] = new_value
            continue

        if not _is_missing(new_value) and current_value != new_value:
            suggested[field] = new_value

    if suggested:
        update_payload["sync_status"] = "needs_review"
        update_payload["pending_payload"] = _build_pending_payload(
            source_id=knowledge_source_id,
            raw_payload=item,
            suggested=suggested,
        )
    else:
        update_payload["sync_status"] = "synced"

    result = (
        supabase.table("service_items")
        .update(update_payload)
        .eq("organization_id", organization_id)
        .eq("id", existing["id"])
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else existing


def _build_option_payload(
    *,
    organization_id: str,
    service_item_id: str,
    knowledge_source_id: str,
    option: dict,
) -> dict:
    option_group = str(option.get("option_group") or "옵션").strip()
    option_value = str(option.get("option_value") or "").strip()

    if not option_value:
        raise ValueError("option_value is required")

    return {
        "organization_id": organization_id,
        "service_item_id": service_item_id,
        "source_id": knowledge_source_id,
        "option_group": option_group,
        "option_value": option_value,
        "description": option.get("description"),
        "additional_price": _to_nullable_int(option.get("additional_price")),
        "additional_duration": _to_nullable_int(option.get("additional_duration")),
        "raw_payload": option,
    }


def _upsert_service_item_option(
    *,
    organization_id: str,
    service_item_id: str,
    knowledge_source_id: str,
    option: dict,
) -> dict:
    """
    service_item_options 테이블에는 상품 옵션을 저장한다.

    정책:
    - 새 옵션이면 pending + is_available=false 로 추가한다.
    - 기존 옵션이 pending이면 AI 추출값으로 갱신한다.
    - 기존 옵션이 approved/is_available=true면 바로 덮어쓰지 않고 변경 후보로 저장한다.
    """
    payload = _build_option_payload(
        organization_id=organization_id,
        service_item_id=service_item_id,
        knowledge_source_id=knowledge_source_id,
        option=option,
    )

    existing = _find_service_item_option(
        organization_id=organization_id,
        service_item_id=service_item_id,
        option_group=payload["option_group"],
        option_value=payload["option_value"],
    )

    if not existing:
        insert_payload = {
            **payload,
            "is_available": False,
            "approval_status": "pending",
            "sync_status": "synced",
            "pending_payload": None,
        }

        result = supabase.table("service_item_options").insert(insert_payload).execute()
        rows = result.data or []

        if not rows:
            raise RuntimeError("Failed to create service item option")

        return rows[0]

    is_approved = (
        existing.get("approval_status") == "approved"
        or bool(existing.get("is_available"))
    )

    if not is_approved:
        update_payload = {
            **payload,
            "is_available": False,
            "approval_status": existing.get("approval_status") or "pending",
            "sync_status": "synced",
            "pending_payload": None,
        }

        result = (
            supabase.table("service_item_options")
            .update(update_payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        return rows[0] if rows else existing

    update_payload: dict[str, Any] = {
        "source_id": knowledge_source_id,
        "raw_payload": option,
    }

    suggested: dict[str, Any] = {}

    for field in ["description", "additional_price", "additional_duration"]:
        current_value = existing.get(field)
        new_value = payload.get(field)

        if _is_missing(current_value) and not _is_missing(new_value):
            update_payload[field] = new_value
            continue

        if not _is_missing(new_value) and current_value != new_value:
            suggested[field] = new_value

    if suggested:
        update_payload["sync_status"] = "needs_review"
        update_payload["pending_payload"] = _build_pending_payload(
            source_id=knowledge_source_id,
            raw_payload=option,
            suggested=suggested,
        )
    else:
        update_payload["sync_status"] = "synced"

    result = (
        supabase.table("service_item_options")
        .update(update_payload)
        .eq("organization_id", organization_id)
        .eq("id", existing["id"])
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else existing


def sync_service_catalog_to_tables(
    *,
    organization_id: str,
    knowledge_source_id: str,
    catalog: dict,
) -> dict:
    """
    AI가 추출한 서비스 카탈로그를 services / service_items / service_item_options 로 저장한다.

    최종 정책:
    - 파일1에서 서비스1,2,3 추출
    - 파일2에서 서비스4,5 추출
    - 기존 목록 삭제/교체하지 않고 계속 누적
    - 같은 이름의 상품은 중복 생성하지 않음
    - 승인된 상품의 변경값은 바로 덮어쓰지 않고 pending_payload로 검토 대기
    """
    parent_service = _upsert_parent_service(
        organization_id=organization_id,
        knowledge_source_id=knowledge_source_id,
        catalog=catalog,
    )

    service_id = parent_service["id"]

    created_or_updated_items: list[dict] = []
    created_or_updated_options: list[dict] = []

    items = catalog.get("items") or []
    if not isinstance(items, list):
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue

        service_item = _upsert_service_item(
            organization_id=organization_id,
            service_id=service_id,
            knowledge_source_id=knowledge_source_id,
            item=item,
        )

        created_or_updated_items.append(service_item)

        options = item.get("options") or []
        if not isinstance(options, list):
            continue

        for option in options:
            if not isinstance(option, dict):
                continue

            service_item_option = _upsert_service_item_option(
                organization_id=organization_id,
                service_item_id=service_item["id"],
                knowledge_source_id=knowledge_source_id,
                option=option,
            )

            created_or_updated_options.append(service_item_option)

    return {
        "service": parent_service,
        "service_id": service_id,
        "service_name": parent_service.get("name"),
        "items_count": len(created_or_updated_items),
        "options_count": len(created_or_updated_options),
        "items": created_or_updated_items,
        "options": created_or_updated_options,
    }


def activate_service_catalog(
    *,
    organization_id: str,
    service_id: str,
) -> dict:
    """
    대분류 서비스 승인 후, 하위 service_items / service_item_options 를 예약 선택지에 노출한다.

    정책:
    - 최초 승인: 아직 approved 된 아이템이 없으면 하위 pending 아이템/옵션을 함께 approved 처리
    - 추가 파일 업로드 이후: 이미 approved 아이템이 있으면 신규 pending 아이템은 자동 승인하지 않음
    """
    existing_active_result = (
        supabase.table("service_items")
        .select("id")
        .eq("organization_id", organization_id)
        .eq("service_id", service_id)
        .eq("approval_status", "approved")
        .eq("is_available", True)
        .limit(1)
        .execute()
    )

    existing_active_items = existing_active_result.data or []

    if existing_active_items:
        return {
            "activated_items_count": 0,
            "activated_options_count": 0,
            "items": [],
            "options": [],
            "skipped_child_activation": True,
            "message": (
                "이미 승인된 서비스 아이템이 있어 신규 추출 아이템은 자동 승인하지 않았습니다. "
                "추가 파일에서 추출된 항목은 pending 상태로 유지됩니다."
            ),
        }

    item_result = (
        supabase.table("service_items")
        .update(
            {
                "is_available": True,
                "approval_status": "approved",
                "sync_status": "synced",
                "pending_payload": None,
                "approved_at": _now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("service_id", service_id)
        .eq("approval_status", "pending")
        .execute()
    )

    items = item_result.data or []
    item_ids = [item["id"] for item in items if item.get("id")]

    options: list[dict] = []

    if item_ids:
        option_result = (
            supabase.table("service_item_options")
            .update(
                {
                    "is_available": True,
                    "approval_status": "approved",
                    "sync_status": "synced",
                    "pending_payload": None,
                    "approved_at": _now_iso(),
                }
            )
            .eq("organization_id", organization_id)
            .in_("service_item_id", item_ids)
            .eq("approval_status", "pending")
            .execute()
        )

        options = option_result.data or []

    return {
        "activated_items_count": len(items),
        "activated_options_count": len(options),
        "items": items,
        "options": options,
        "skipped_child_activation": False,
    }

def activate_service_catalog(
    *,
    organization_id: str,
    service_id: str,
) -> dict:
    """
    대분류 서비스 승인 후, 하위 service_items / service_item_options 를 예약 선택지에 노출한다.

    정책:
    - 최초 승인: 아직 approved 된 아이템이 없으면 하위 pending 아이템/옵션을 함께 approved 처리
    - 추가 파일 업로드 이후: 이미 approved 아이템이 있으면 신규 pending 아이템은 자동 승인하지 않음
    """
    existing_active_result = (
        supabase.table("service_items")
        .select("id")
        .eq("organization_id", organization_id)
        .eq("service_id", service_id)
        .eq("approval_status", "approved")
        .eq("is_available", True)
        .limit(1)
        .execute()
    )

    existing_active_items = existing_active_result.data or []

    if existing_active_items:
        return {
            "activated_items_count": 0,
            "activated_options_count": 0,
            "items": [],
            "options": [],
            "skipped_child_activation": True,
            "message": (
                "이미 승인된 서비스 아이템이 있어 신규 추출 아이템은 자동 승인하지 않았습니다. "
                "추가 파일에서 추출된 항목은 pending 상태로 유지됩니다."
            ),
        }

    item_result = (
        supabase.table("service_items")
        .update(
            {
                "is_available": True,
                "approval_status": "approved",
                "sync_status": "synced",
                "pending_payload": None,
                "approved_at": _now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("service_id", service_id)
        .eq("approval_status", "pending")
        .execute()
    )

    items = item_result.data or []
    item_ids = [item["id"] for item in items if item.get("id")]

    options: list[dict] = []

    if item_ids:
        option_result = (
            supabase.table("service_item_options")
            .update(
                {
                    "is_available": True,
                    "approval_status": "approved",
                    "sync_status": "synced",
                    "pending_payload": None,
                    "approved_at": _now_iso(),
                }
            )
            .eq("organization_id", organization_id)
            .in_("service_item_id", item_ids)
            .eq("approval_status", "pending")
            .execute()
        )

        options = option_result.data or []

    return {
        "activated_items_count": len(items),
        "activated_options_count": len(options),
        "items": items,
        "options": options,
        "skipped_child_activation": False,
    }