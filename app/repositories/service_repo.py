import hashlib
import json
from datetime import datetime, timezone

from app.core.db import supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_extracted_hash(payload: dict) -> str:
    """
    AI 추출 결과가 이전과 같은지 비교하기 위한 해시.
    """
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_service_by_name(
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


def sync_extracted_service_to_pending(
    *,
    organization_id: str,
    knowledge_source_id: str,
    extracted_service: dict,
) -> dict:
    """
    AI가 지식에서 추출한 서비스 후보를 services 테이블에 반영한다.

    상태 정책:
    - 기존 없음: pending + is_active=false로 생성
    - 기존 pending: 최신 추출값으로 업데이트
    - 기존 approved: 실제 서비스 값은 유지, pending_payload에 변경 후보 저장
    - 기존 rejected: 자동 승인하지 않고 pending_payload에 후보만 저장
    """
    name = str(extracted_service.get("name") or "").strip()

    if not name:
        raise ValueError("service name is required")

    raw_payload = extracted_service.get("raw_payload") or extracted_service
    extracted_hash = build_extracted_hash(raw_payload)
    now = _now_iso()

    existing = get_service_by_name(
        organization_id=organization_id,
        name=name,
    )

    base_payload = {
        "organization_id": organization_id,
        "name": name,
        "description": extracted_service.get("description"),
        "price": extracted_service.get("price"),
        "currency": "KRW",
        "duration_minutes": extracted_service.get("duration_minutes"),
        "source_type": "knowledge",
        "source_id": knowledge_source_id,
        "confidence": extracted_service.get("confidence"),
        "raw_payload": raw_payload,
        "extracted_hash": extracted_hash,
        "last_extracted_at": now,
    }

    # 1. 기존 서비스가 없으면 pending 후보로 새로 생성
    if not existing:
        insert_payload = {
            **base_payload,
            "is_active": False,
            "approval_status": "pending",
            "sync_status": "synced",
        }

        result = supabase.table("services").insert(insert_payload).execute()
        rows = result.data or []

        if not rows:
            raise RuntimeError("Failed to create pending service")

        return rows[0]

    approval_status = existing.get("approval_status") or "approved"

    # 2. 이미 pending이면 최신 추출값으로 덮어쓰기
    if approval_status == "pending":
        update_payload = {
            **base_payload,
            "is_active": False,
            "approval_status": "pending",
            "sync_status": "synced",
            "pending_payload": None,
        }

        result = (
            supabase.table("services")
            .update(update_payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        if not rows:
            raise RuntimeError("Failed to update pending service")

        return rows[0]

    # 3. approved는 실제 예약 상품이므로 바로 덮어쓰지 않음
    if approval_status == "approved":
        update_payload = {
            "pending_payload": raw_payload,
            "confidence": extracted_service.get("confidence"),
            "extracted_hash": extracted_hash,
            "last_extracted_at": now,
            "sync_status": "needs_review",
        }

        result = (
            supabase.table("services")
            .update(update_payload)
            .eq("organization_id", organization_id)
            .eq("id", existing["id"])
            .execute()
        )

        rows = result.data or []
        if not rows:
            raise RuntimeError("Failed to mark approved service as needs_review")

        return rows[0]

    # 4. rejected는 자동 재활성화하지 않고 검토 후보만 남김
    update_payload = {
        "pending_payload": raw_payload,
        "confidence": extracted_service.get("confidence"),
        "extracted_hash": extracted_hash,
        "last_extracted_at": now,
        "sync_status": "needs_review",
    }

    result = (
        supabase.table("services")
        .update(update_payload)
        .eq("organization_id", organization_id)
        .eq("id", existing["id"])
        .execute()
    )

    rows = result.data or []
    if not rows:
        raise RuntimeError("Failed to update rejected service pending payload")

    return rows[0]


def list_services_by_source(
    *,
    organization_id: str,
    source_id: str,
) -> list[dict]:
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("source_type", "knowledge")
        .eq("source_id", source_id)
        .execute()
    )

    return result.data or []


def mark_source_services_stale(
    *,
    organization_id: str,
    source_id: str,
    extracted_names: list[str],
) -> list[dict]:
    """
    이전에는 이 지식에서 추출됐는데,
    이번 재추출 결과에는 더 이상 없는 서비스들을 stale 처리한다.

    단, 자동 삭제/비활성화는 하지 않는다.
    """
    existing_services = list_services_by_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    extracted_name_set = set(extracted_names)
    stale_services = []

    for service in existing_services:
        service_name = service.get("name")

        if service_name in extracted_name_set:
            continue

        result = (
            supabase.table("services")
            .update({"sync_status": "stale"})
            .eq("organization_id", organization_id)
            .eq("id", service["id"])
            .execute()
        )

        rows = result.data or []
        if rows:
            stale_services.append(rows[0])

    return stale_services


def list_services(
    organization_id: str,
    only_active: bool = True,
) -> list[dict]:
    """
    특정 조직의 서비스 대분류 목록을 조회한다.

    기존 services 테이블은 지식 파일에서 AI가 추출한 후보도 같이 저장하므로,
    기본적으로 실제 승인/활성화된 서비스만 조회한다.
    """
    query = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=False)
    )

    if only_active:
        query = (
            query
            .eq("is_active", True)
            .eq("approval_status", "approved")
        )

    result = query.execute()
    return result.data or []


def get_service(
    organization_id: str,
    service_id: str,
) -> dict | None:
    """
    특정 서비스 대분류 1개를 조회한다.
    """
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", service_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def list_service_items(
    organization_id: str,
    service_id: str | None = None,
) -> list[dict]:
    """
    특정 조직의 실제 예약 상품 목록을 조회한다.
    예: 이사 청소, 화장실 청소, 세팅 펌
    """
    query = (
        supabase.table("service_items")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("is_available", True)
        .order("created_at", desc=False)
    )

    if service_id:
        query = query.eq("service_id", service_id)

    result = query.execute()
    return result.data or []


def get_service_item(
    organization_id: str,
    service_item_id: str,
) -> dict | None:
    """
    특정 서비스 아이템 1개를 조회한다.
    """
    result = (
        supabase.table("service_items")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", service_item_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def list_service_item_options(
    organization_id: str,
    service_item_id: str,
) -> list[dict]:
    """
    특정 서비스 아이템의 옵션 목록을 조회한다.
    예: 24평형, 34평형, 베란다 확장형
    """
    result = (
        supabase.table("service_item_options")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("service_item_id", service_item_id)
        .eq("is_available", True)
        .order("option_group", desc=False)
        .order("option_value", desc=False)
        .execute()
    )

    return result.data or []

def list_active_services(
    organization_id: str,
) -> list[dict]:
    """
    예약/화면에서 사용할 활성 서비스 대분류 목록 조회.
    """
    result = (
        supabase.table("services")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("is_active", True)
        .eq("approval_status", "approved")
        .order("created_at", desc=False)
        .execute()
    )

    return result.data or []


def calculate_service_price(
    *,
    organization_id: str,
    service_item_id: str,
    option_ids: list[str] | None = None,
) -> dict:
    """
    서비스 아이템 기본가 + 선택 옵션 가격을 합산한다.

    반환값은 이후 reservations.ordered_summary에 그대로 저장할 수 있는
    스냅샷 형태를 포함한다.
    """
    option_ids = option_ids or []

    item = get_service_item(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    if not item:
        raise ValueError("Service item not found")

    base_price = int(item.get("base_price") or 0)
    base_duration = int(item.get("duration_minutes") or 0)

    options: list[dict] = []

    if option_ids:
        result = (
            supabase.table("service_item_options")
            .select("*")
            .eq("organization_id", organization_id)
            .eq("service_item_id", service_item_id)
            .eq("is_available", True)
            .in_("id", option_ids)
            .execute()
        )

        options = result.data or []

        if len(options) != len(option_ids):
            raise ValueError("Some service item options were not found")

    options_price = sum(int(option.get("additional_price") or 0) for option in options)
    options_duration = sum(int(option.get("additional_duration") or 0) for option in options)

    total_price = base_price + options_price
    total_duration_minutes = base_duration + options_duration

    ordered_summary = {
        "service_item": {
            "id": item.get("id"),
            "name": item.get("name"),
            "description": item.get("description"),
            "base_price": base_price,
            "duration_minutes": base_duration,
        },
        "options": [
            {
                "id": option.get("id"),
                "option_group": option.get("option_group"),
                "option_value": option.get("option_value"),
                "additional_price": int(option.get("additional_price") or 0),
                "additional_duration": int(option.get("additional_duration") or 0),
            }
            for option in options
        ],
        "total_price": total_price,
        "total_duration_minutes": total_duration_minutes,
    }

    return {
        "organization_id": organization_id,
        "service_item_id": service_item_id,
        "option_ids": option_ids,
        "base_price": base_price,
        "options_price": options_price,
        "total_price": total_price,
        "base_duration_minutes": base_duration,
        "options_duration_minutes": options_duration,
        "total_duration_minutes": total_duration_minutes,
        "ordered_summary": ordered_summary,
    }

def _normalize_match_text(value: str | None) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _is_empty_option_text(value: str | None) -> bool:
    normalized = _normalize_match_text(value)
    return normalized in {
        "",
        "없음",
        "없어요",
        "없어",
        "선택안함",
        "선택없음",
        "no",
        "none",
        "null",
        "[]",
    }


def _match_by_name_or_text(
    *,
    user_text: str,
    candidates: list[dict],
    name_key: str,
) -> list[dict]:
    user_norm = _normalize_match_text(user_text)

    if not user_norm:
        return []

    exact_matches = [
        candidate
        for candidate in candidates
        if _normalize_match_text(candidate.get(name_key)) == user_norm
    ]

    if exact_matches:
        return exact_matches

    contains_matches = [
        candidate
        for candidate in candidates
        if _normalize_match_text(candidate.get(name_key)) in user_norm
    ]

    if contains_matches:
        return contains_matches

    reverse_contains_matches = [
        candidate
        for candidate in candidates
        if user_norm in _normalize_match_text(candidate.get(name_key))
    ]

    return reverse_contains_matches


def resolve_service_item_by_name(
    *,
    organization_id: str,
    user_text: str,
    service_id: str | None = None,
) -> dict:
    items = list_service_items(
        organization_id=organization_id,
        service_id=service_id,
    )

    matches = _match_by_name_or_text(
        user_text=user_text,
        candidates=items,
        name_key="name",
    )

    if len(matches) == 1:
        item = matches[0]

        return {
            "ok": True,
            "resolved": True,
            "service_item_id": item.get("id"),
            "service_item_name": item.get("name"),
            "service_item": item,
            "candidates": [],
            "message": None,
        }

    if len(matches) > 1:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "ambiguous_service_item",
            "message": "예약할 서비스가 여러 개로 해석됩니다. 정확한 서비스명을 다시 입력해주세요.",
            "service_item_id": None,
            "service_item_name": None,
            "service_item": None,
            "candidates": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "base_price": item.get("base_price"),
                    "duration_minutes": item.get("duration_minutes"),
                }
                for item in matches
            ],
        }

    return {
        "ok": False,
        "resolved": False,
        "error_code": "service_item_not_found",
        "message": "입력하신 서비스 아이템을 찾지 못했습니다. 다시 입력해주세요.",
        "service_item_id": None,
        "service_item_name": None,
        "service_item": None,
        "candidates": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "base_price": item.get("base_price"),
                "duration_minutes": item.get("duration_minutes"),
            }
            for item in items
        ],
    }


def resolve_service_options_by_name(
    *,
    organization_id: str,
    service_item_id: str,
    user_text: str,
) -> dict:
    if _is_empty_option_text(user_text):
        return {
            "ok": True,
            "resolved": True,
            "selected_option_ids": [],
            "selected_options": [],
            "unmatched_text": [],
            "message": "선택한 옵션이 없습니다.",
        }

    options = list_service_item_options(
        organization_id=organization_id,
        service_item_id=service_item_id,
    )

    normalized_text = _normalize_match_text(user_text)

    matched_options = []

    for option in options:
        option_value_norm = _normalize_match_text(option.get("option_value"))

        if option_value_norm and option_value_norm in normalized_text:
            matched_options.append(option)

    if not matched_options:
        parts = [
            part.strip()
            for part in str(user_text or "")
            .replace("이랑", ",")
            .replace("하고", ",")
            .replace("랑", ",")
            .split(",")
            if part.strip()
        ]

        for part in parts:
            matches = _match_by_name_or_text(
                user_text=part,
                candidates=options,
                name_key="option_value",
            )

            for match in matches:
                if match not in matched_options:
                    matched_options.append(match)

    if not matched_options:
        return {
            "ok": False,
            "resolved": False,
            "error_code": "service_options_not_found",
            "message": "입력하신 옵션을 찾지 못했습니다. 다시 입력해주세요.",
            "selected_option_ids": [],
            "selected_options": [],
            "candidates": [
                {
                    "id": option.get("id"),
                    "option_group": option.get("option_group"),
                    "option_value": option.get("option_value"),
                    "additional_price": option.get("additional_price"),
                    "additional_duration": option.get("additional_duration"),
                }
                for option in options
            ],
        }

    return {
        "ok": True,
        "resolved": True,
        "selected_option_ids": [
            str(option.get("id"))
            for option in matched_options
            if option.get("id")
        ],
        "selected_options": [
            {
                "id": option.get("id"),
                "option_group": option.get("option_group"),
                "option_value": option.get("option_value"),
                "additional_price": option.get("additional_price"),
                "additional_duration": option.get("additional_duration"),
            }
            for option in matched_options
        ],
        "unmatched_text": [],
        "message": None,
    }

