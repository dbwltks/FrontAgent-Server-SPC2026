from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.db import supabase
from app.repositories.product_repo import (
    ProductNotFoundError,
    ProductRepoError,
    ProductStockError,
    check_product_stock,
    decrease_product_stock,
    get_product_or_raise,
)


class OrderRepoError(Exception):
    """상품 주문 repository 공통 예외입니다."""


class OrderNotFoundError(OrderRepoError):
    """주문을 찾지 못했을 때 사용합니다."""


class OrderStatusError(OrderRepoError):
    """주문 상태 변경이 불가능할 때 사용합니다."""


ORDER_STATUSES = [
    "requested",
    "confirmed",
    "preparing",
    "shipped",
    "delivered",
    "cancelled",
]

PAYMENT_STATUSES = [
    "unpaid",
    "paid",
    "failed",
    "refunded",
]

DELIVERY_STATUSES = [
    "pending",
    "preparing",
    "shipped",
    "delivered",
    "failed",
]


def create_order(
    organization_id: str,
    customer_name: str,
    customer_phone: str,
    items: list[dict],
    customer_email: str | None = None,
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    postal_code: str | None = None,
    address_line1: str | None = None,
    address_line2: str | None = None,
    delivery_memo: str | None = None,
    source_channel: str = "web_chat",
    memo: str | None = None,
) -> dict:
    """
    상품 주문 요청을 생성합니다.

    주의:
    - 기본 상태는 requested 입니다.
    - 이 단계에서는 주문 요청만 저장합니다.
    - 실제 재고 차감은 confirm_order()에서 처리합니다.
      이유: requested 상태는 아직 관리자 확인 전이기 때문입니다.

    items 예시:
    [
        {
            "product_id": "uuid",
            "quantity": 2,
            "selected_options": {"color": "black", "size": "L"}
        }
    ]
    """

    if not customer_name:
        raise OrderRepoError("customer_name is required")

    if not customer_phone:
        raise OrderRepoError("customer_phone is required")

    if not items:
        raise OrderRepoError("items are required")

    normalized_items = _prepare_order_items(
        organization_id=organization_id,
        items=items,
    )

    total_amount = sum(item["total_price"] for item in normalized_items)

    order_code = _generate_order_code()

    order_insert_data = {
        "organization_id": organization_id,
        "order_code": order_code,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        "order_status": "requested",
        "payment_status": "unpaid",
        "delivery_status": "pending",
        "recipient_name": recipient_name or customer_name,
        "recipient_phone": recipient_phone or customer_phone,
        "postal_code": postal_code,
        "address_line1": address_line1,
        "address_line2": address_line2,
        "delivery_memo": delivery_memo,
        "total_amount": total_amount,
        "currency": "KRW",
        "source_channel": source_channel,
        "memo": memo,
    }

    order_result = supabase.table("orders").insert(order_insert_data).execute()
    order_rows = order_result.data or []

    if not order_rows:
        raise OrderRepoError("Failed to create order")

    order = order_rows[0]
    order_id = order["id"]

    order_item_rows = []

    for item in normalized_items:
        order_item_rows.append(
            {
                "organization_id": organization_id,
                "order_id": order_id,
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "product_sku": item.get("product_sku"),
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "total_price": item["total_price"],
                "selected_options": item.get("selected_options") or {},
            }
        )

    items_result = supabase.table("order_items").insert(order_item_rows).execute()
    inserted_items = items_result.data or []

    if len(inserted_items) != len(order_item_rows):
        raise OrderRepoError("Failed to create order items")

    return {
        **order,
        "items": inserted_items,
    }


def list_orders(
    organization_id: str,
    order_status: str | None = None,
    delivery_status: str | None = None,
    customer_phone: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    주문 목록을 조회합니다.

    관리자 화면 또는 주문 조회 Function Node에서 사용합니다.
    """

    query = (
        supabase.table("orders")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if order_status:
        query = query.eq("order_status", order_status)

    if delivery_status:
        query = query.eq("delivery_status", delivery_status)

    if customer_phone:
        query = query.eq("customer_phone", customer_phone)

    result = query.execute()
    return result.data or []


def get_order(
    organization_id: str,
    order_id: str,
    include_items: bool = True,
) -> dict | None:
    """
    주문 상세를 조회합니다.
    """

    order_result = (
        supabase.table("orders")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", order_id)
        .limit(1)
        .execute()
    )

    order_rows = order_result.data or []

    if not order_rows:
        return None

    order = order_rows[0]

    if not include_items:
        return order

    item_result = (
        supabase.table("order_items")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("order_id", order_id)
        .order("created_at")
        .execute()
    )

    order["items"] = item_result.data or []
    return order


def get_order_or_raise(
    organization_id: str,
    order_id: str,
    include_items: bool = True,
) -> dict:
    """
    주문을 조회하고, 없으면 OrderNotFoundError를 발생시킵니다.
    """

    order = get_order(
        organization_id=organization_id,
        order_id=order_id,
        include_items=include_items,
    )

    if not order:
        raise OrderNotFoundError("Order not found")

    return order


def lookup_orders_by_phone(
    organization_id: str,
    customer_phone: str,
    limit: int = 10,
) -> list[dict]:
    """
    고객 연락처 기준으로 주문을 조회합니다.

    고객이 "내 주문 어떻게 됐어?"라고 물어볼 때 사용합니다.
    """

    orders = list_orders(
        organization_id=organization_id,
        customer_phone=customer_phone,
        limit=limit,
    )

    results = []

    for order in orders:
        order_detail = get_order(
            organization_id=organization_id,
            order_id=order["id"],
            include_items=True,
        )
        if order_detail:
            results.append(order_detail)

    return results


def confirm_order(
    organization_id: str,
    order_id: str,
) -> dict:
    """
    주문을 confirmed 상태로 변경합니다.

    이때 상품 재고를 차감합니다.
    requested 상태에서만 확정 가능합니다.
    """

    order = get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=True,
    )

    if order["order_status"] == "confirmed":
        return order

    if order["order_status"] != "requested":
        raise OrderStatusError(
            f"Cannot confirm order with status: {order['order_status']}"
        )

    items = order.get("items") or []

    if not items:
        raise OrderRepoError("Order has no items")

    # 1. 전체 상품 재고를 먼저 확인합니다.
    for item in items:
        product_id = item.get("product_id")
        quantity = int(item.get("quantity") or 0)

        if not product_id:
            raise OrderRepoError("Order item product_id is missing")

        stock_result = check_product_stock(
            organization_id=organization_id,
            product_id=product_id,
            quantity=quantity,
        )

        if not stock_result["available"]:
            raise ProductStockError(
                stock_result.get("reason") or "Product stock unavailable"
            )

    # 2. 모든 상품이 가능할 때 재고를 차감합니다.
    for item in items:
        decrease_product_stock(
            organization_id=organization_id,
            product_id=item["product_id"],
            quantity=int(item["quantity"]),
        )

    result = (
        supabase.table("orders")
        .update(
            {
                "order_status": "confirmed",
                "delivery_status": "preparing",
                "updated_at": _now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", order_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise OrderRepoError("Failed to confirm order")

    return get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=True,
    )


def cancel_order(
    organization_id: str,
    order_id: str,
) -> dict:
    """
    주문을 취소 상태로 변경합니다.

    MVP에서는 재고 복구는 하지 않습니다.
    나중에 confirmed 이후 취소 시 재고 복구 로직을 추가하면 됩니다.
    """

    order = get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=False,
    )

    if order["order_status"] in ["cancelled", "delivered"]:
        raise OrderStatusError(
            f"Cannot cancel order with status: {order['order_status']}"
        )

    result = (
        supabase.table("orders")
        .update(
            {
                "order_status": "cancelled",
                "delivery_status": "failed",
                "cancelled_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", order_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise OrderRepoError("Failed to cancel order")

    return get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=True,
    )


def update_delivery_info(
    organization_id: str,
    order_id: str,
    courier_name: str | None = None,
    tracking_number: str | None = None,
    delivery_status: str | None = None,
) -> dict:
    """
    택배사, 송장번호, 배송 상태를 업데이트합니다.
    """

    if delivery_status and delivery_status not in DELIVERY_STATUSES:
        raise OrderStatusError(f"Invalid delivery status: {delivery_status}")

    update_data: dict = {
        "updated_at": _now_iso(),
    }

    if courier_name is not None:
        update_data["courier_name"] = courier_name

    if tracking_number is not None:
        update_data["tracking_number"] = tracking_number

    if delivery_status is not None:
        update_data["delivery_status"] = delivery_status

    result = (
        supabase.table("orders")
        .update(update_data)
        .eq("organization_id", organization_id)
        .eq("id", order_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise OrderRepoError("Failed to update delivery info")

    return get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=True,
    )


def mark_order_shipped(
    organization_id: str,
    order_id: str,
    courier_name: str | None = None,
    tracking_number: str | None = None,
) -> dict:
    """
    주문을 배송 중 상태로 변경합니다.
    """

    order = get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=False,
    )

    if order["order_status"] not in ["confirmed", "preparing", "shipped"]:
        raise OrderStatusError(
            f"Cannot ship order with status: {order['order_status']}"
        )

    update_data = {
        "order_status": "shipped",
        "delivery_status": "shipped",
        "shipped_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    if courier_name is not None:
        update_data["courier_name"] = courier_name

    if tracking_number is not None:
        update_data["tracking_number"] = tracking_number

    result = (
        supabase.table("orders")
        .update(update_data)
        .eq("organization_id", organization_id)
        .eq("id", order_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise OrderRepoError("Failed to mark order as shipped")

    return get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=True,
    )


def mark_order_delivered(
    organization_id: str,
    order_id: str,
) -> dict:
    """
    주문을 배송 완료 상태로 변경합니다.
    """

    order = get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=False,
    )

    if order["order_status"] != "shipped":
        raise OrderStatusError(
            f"Cannot deliver order with status: {order['order_status']}"
        )

    result = (
        supabase.table("orders")
        .update(
            {
                "order_status": "delivered",
                "delivery_status": "delivered",
                "delivered_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )
        .eq("organization_id", organization_id)
        .eq("id", order_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise OrderRepoError("Failed to mark order as delivered")

    return get_order_or_raise(
        organization_id=organization_id,
        order_id=order_id,
        include_items=True,
    )


def _prepare_order_items(
    organization_id: str,
    items: list[dict],
) -> list[dict]:
    """
    주문 요청 item 데이터를 DB 저장용으로 정규화합니다.

    product_id 기준으로 products에서 상품명, sku, 가격을 가져와
    order_items에 snapshot 형태로 저장할 데이터를 만듭니다.
    """

    normalized_items: list[dict] = []

    for item in items:
        product_id = item.get("product_id")
        quantity = int(item.get("quantity") or 1)
        selected_options = item.get("selected_options") or {}

        if not product_id:
            raise OrderRepoError("product_id is required")

        if quantity <= 0:
            raise OrderRepoError("quantity must be greater than 0")

        product = get_product_or_raise(
            organization_id=organization_id,
            product_id=product_id,
        )

        if not product.get("is_active", True):
            raise ProductNotFoundError("Product is inactive")

        stock_result = check_product_stock(
            organization_id=organization_id,
            product_id=product_id,
            quantity=quantity,
        )

        if not stock_result["available"]:
            raise ProductStockError(
                stock_result.get("reason") or "Product stock unavailable"
            )

        unit_price = int(product.get("price") or 0)
        total_price = unit_price * quantity

        normalized_items.append(
            {
                "product_id": product_id,
                "product_name": product["name"],
                "product_sku": product.get("sku"),
                "quantity": quantity,
                "unit_price": unit_price,
                "total_price": total_price,
                "selected_options": selected_options,
            }
        )

    return normalized_items


def _generate_order_code() -> str:
    """
    MVP용 주문번호 생성 함수입니다.

    예:
    ORD-20260625-153012

    완전한 고유성을 원하면 나중에 DB sequence 또는 RPC로 바꾸는 것이 좋습니다.
    """

    now = datetime.now(tz=ZoneInfo("Asia/Seoul"))
    return f"ORD-{now.strftime('%Y%m%d-%H%M%S')}"


def _now_iso() -> str:
    """
    Asia/Seoul 기준 현재 시간을 ISO 문자열로 반환합니다.
    """

    return datetime.now(tz=ZoneInfo("Asia/Seoul")).isoformat()