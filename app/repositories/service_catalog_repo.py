from typing import Any

from app.core.db import supabase


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


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
    예: 홈 클리닝
    """

    service_name = str(catalog.get("service_name") or "").strip()
    if not service_name:
        raise ValueError("service_name is required")

    existing = _find_service_by_name(
        organization_id=organization_id,
        name=service_name,
    )

    raw_payload = catalog

    if existing:
        update_payload = {
            "description": catalog.get("description"),
            "price": None,
            "duration_minutes": None,
            "source_type": "knowledge",
            "source_id": knowledge_source_id,
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

        # 대분류 서비스는 직접 예약 상품이 아니므로 가격/소요시간을 확정하지 않는다.
        # DB 제약조건상 0분은 허용되지 않을 수 있으므로 None으로 저장한다.
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


def _upsert_service_item(
    *,
    organization_id: str,
    service_id: str,
    item: dict,
    is_available: bool,
) -> dict:
    """
    service_items 테이블에는 실제 예약 가능한 세부 상품을 저장한다.
    예: 이사 청소, 화장실 청소, 베란다 청소
    """

    item_name = str(item.get("name") or "").strip()
    if not item_name:
        raise ValueError("service item name is required")

    payload = {
        "organization_id": organization_id,
        "service_id": service_id,
        "name": item_name,
        "description": item.get("description"),
        "base_price": _to_int(item.get("base_price"), 0),
        "duration_minutes": _to_int(item.get("duration_minutes"), 0),
        "is_available": is_available,
        "raw_payload": item,
    }

    existing = _find_service_item_by_name(
        organization_id=organization_id,
        service_id=service_id,
        name=item_name,
    )

    if existing:
        result = (
            supabase.table("service_items")
            .update(payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        return rows[0] if rows else existing

    result = supabase.table("service_items").insert(payload).execute()
    rows = result.data or []

    if not rows:
        raise RuntimeError("Failed to create service item")

    return rows[0]


def _upsert_service_item_option(
    *,
    organization_id: str,
    service_item_id: str,
    option: dict,
    is_available: bool,
) -> dict:
    """
    service_item_options 테이블에는 세부 상품 옵션을 저장한다.
    예: 24평형, 34평형, 심한 곰팡이
    """

    option_group = str(option.get("option_group") or "옵션").strip()
    option_value = str(option.get("option_value") or "").strip()

    if not option_value:
        raise ValueError("option_value is required")

    payload = {
        "organization_id": organization_id,
        "service_item_id": service_item_id,
        "option_group": option_group,
        "option_value": option_value,
        "description": option.get("description"),
        "additional_price": _to_int(option.get("additional_price"), 0),
        "additional_duration": _to_int(option.get("additional_duration"), 0),
        "is_available": is_available,
        "raw_payload": option,
    }

    existing = _find_service_item_option(
        organization_id=organization_id,
        service_item_id=service_item_id,
        option_group=option_group,
        option_value=option_value,
    )

    if existing:
        result = (
            supabase.table("service_item_options")
            .update(payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        return rows[0] if rows else existing

    result = supabase.table("service_item_options").insert(payload).execute()
    rows = result.data or []

    if not rows:
        raise RuntimeError("Failed to create service item option")

    return rows[0]


def sync_service_catalog_to_tables(
    *,
    organization_id: str,
    knowledge_source_id: str,
    catalog: dict,
) -> dict:
    """
    AI가 추출한 서비스 카탈로그를
    services / service_items / service_item_options 로 정규화 저장한다.
    """

    parent_service = _upsert_parent_service(
        organization_id=organization_id,
        knowledge_source_id=knowledge_source_id,
        catalog=catalog,
    )

    service_id = parent_service["id"]

    service_is_active = (
        parent_service.get("approval_status") == "approved"
        and bool(parent_service.get("is_active"))
    )

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
            item=item,
            is_available=service_is_active,
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
                option=option,
                is_available=service_is_active,
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
    대분류 서비스 승인 후,
    하위 service_items / service_item_options 를 예약 선택지에 노출한다.
    """

    item_result = (
        supabase.table("service_items")
        .update({"is_available": True})
        .eq("organization_id", organization_id)
        .eq("service_id", service_id)
        .execute()
    )

    items = item_result.data or []
    item_ids = [item["id"] for item in items if item.get("id")]

    options: list[dict] = []

    if item_ids:
        option_result = (
            supabase.table("service_item_options")
            .update({"is_available": True})
            .eq("organization_id", organization_id)
            .in_("service_item_id", item_ids)
            .execute()
        )

        options = option_result.data or []

    return {
        "activated_items_count": len(items),
        "activated_options_count": len(options),
        "items": items,
        "options": options,
    }