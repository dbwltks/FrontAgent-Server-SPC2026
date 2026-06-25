from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.repositories.order_repo import (
    OrderNotFoundError,
    OrderRepoError,
    OrderStatusError,
    cancel_order,
    confirm_order,
    create_order,
    get_order,
    list_orders,
    lookup_orders_by_phone,
    mark_order_delivered,
    mark_order_shipped,
    update_delivery_info,
)
from app.repositories.product_repo import (
    ProductNotFoundError,
    ProductRepoError,
    ProductStockError,
)

router = APIRouter(tags=["Orders"])


class OrderItemRequest(BaseModel):
    product_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    quantity: int = Field(default=1, ge=1, example=2)
    selected_options: dict[str, Any] = Field(
        default_factory=dict,
        example={"color": "black", "size": "L"},
    )


class OrderCreateRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )

    customer_name: str = Field(..., example="김민수")
    customer_phone: str = Field(..., example="010-1234-5678")
    customer_email: str | None = Field(default=None, example="customer@example.com")

    items: list[OrderItemRequest] = Field(
        ...,
        example=[
            {
                "product_id": "00000000-0000-0000-0000-000000000000",
                "quantity": 2,
                "selected_options": {"color": "black", "size": "L"},
            }
        ],
    )

    recipient_name: str | None = Field(default=None, example="김민수")
    recipient_phone: str | None = Field(default=None, example="010-1234-5678")

    postal_code: str | None = Field(default=None, example="06236")
    address_line1: str | None = Field(default=None, example="서울시 강남구 테헤란로 123")
    address_line2: str | None = Field(default=None, example="101동 1001호")
    delivery_memo: str | None = Field(default=None, example="문 앞에 놓아주세요.")

    source_channel: str = Field(default="web_chat", example="web_chat")
    memo: str | None = Field(default=None, example="고객이 빠른 배송을 요청함")


class DeliveryInfoUpdateRequest(BaseModel):
    courier_name: str | None = Field(default=None, example="CJ대한통운")
    tracking_number: str | None = Field(default=None, example="123456789012")
    delivery_status: str | None = Field(default=None, example="preparing")


class ShipOrderRequest(BaseModel):
    courier_name: str | None = Field(default=None, example="CJ대한통운")
    tracking_number: str | None = Field(default=None, example="123456789012")


def _handle_order_error(error: Exception) -> None:
    print("Order API ERROR:", type(error).__name__, str(error))

    if isinstance(error, OrderNotFoundError):
        raise HTTPException(status_code=404, detail=str(error))

    if isinstance(error, ProductNotFoundError):
        raise HTTPException(status_code=404, detail=str(error))

    if isinstance(error, ProductStockError):
        raise HTTPException(status_code=409, detail=str(error))

    if isinstance(error, OrderStatusError):
        raise HTTPException(status_code=409, detail=str(error))

    if isinstance(error, OrderRepoError):
        raise HTTPException(status_code=400, detail=str(error))

    if isinstance(error, ProductRepoError):
        raise HTTPException(status_code=400, detail=str(error))

    raise HTTPException(status_code=500, detail="Order API error")


@router.post("/orders")
def create_order_api(
    request: OrderCreateRequest,
) -> dict[str, Any]:
    """
    상품 주문 요청을 생성한다.

    기본 상태:
    - order_status = requested
    - payment_status = unpaid
    - delivery_status = pending
    """

    try:
        order = create_order(
            organization_id=request.organization_id,
            customer_name=request.customer_name,
            customer_phone=request.customer_phone,
            customer_email=request.customer_email,
            items=[item.model_dump() for item in request.items],
            recipient_name=request.recipient_name,
            recipient_phone=request.recipient_phone,
            postal_code=request.postal_code,
            address_line1=request.address_line1,
            address_line2=request.address_line2,
            delivery_memo=request.delivery_memo,
            source_channel=request.source_channel,
            memo=request.memo,
        )

        return {
            "order_id": order["id"],
            "id": order["id"],
            "order_code": order.get("order_code"),
            "order_status": order["order_status"],
            "delivery_status": order["delivery_status"],
            "message": "주문 요청이 접수되었습니다.",
            "order": order,
        }

    except Exception as error:
        _handle_order_error(error)


@router.get("/orders")
def list_orders_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    order_status: str | None = Query(default=None, examples=["requested"]),
    delivery_status: str | None = Query(default=None, examples=["pending"]),
    customer_phone: str | None = Query(default=None, examples=["010-1234-5678"]),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """
    주문 목록을 조회한다.
    """

    try:
        items = list_orders(
            organization_id=organization_id,
            order_status=order_status,
            delivery_status=delivery_status,
            customer_phone=customer_phone,
            limit=limit,
        )

        return {
            "organization_id": organization_id,
            "count": len(items),
            "items": items,
        }

    except Exception as error:
        _handle_order_error(error)


@router.get("/orders/lookup/by-phone")
def lookup_orders_by_phone_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    customer_phone: str = Query(..., examples=["010-1234-5678"]),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """
    고객 연락처 기준으로 주문을 조회한다.

    고객이 "내 주문 어떻게 됐어?"라고 물어볼 때 사용할 수 있다.
    """

    try:
        items = lookup_orders_by_phone(
            organization_id=organization_id,
            customer_phone=customer_phone,
            limit=limit,
        )

        return {
            "organization_id": organization_id,
            "customer_phone": customer_phone,
            "count": len(items),
            "items": items,
        }

    except Exception as error:
        _handle_order_error(error)


@router.get("/orders/{order_id}")
def get_order_api(
    order_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    주문 상세 정보를 조회한다.
    """

    try:
        order = get_order(
            organization_id=organization_id,
            order_id=order_id,
            include_items=True,
        )

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        return order

    except HTTPException:
        raise

    except Exception as error:
        _handle_order_error(error)


@router.patch("/orders/{order_id}/confirm")
def confirm_order_api(
    order_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    주문을 확정한다.

    상태 변경:
    - requested -> confirmed
    - delivery_status -> preparing

    이때 상품 재고를 차감한다.
    """

    try:
        order = confirm_order(
            organization_id=organization_id,
            order_id=order_id,
        )

        return {
            "id": order["id"],
            "order_status": order["order_status"],
            "delivery_status": order["delivery_status"],
            "message": "주문이 확정되었습니다.",
            "order": order,
        }

    except Exception as error:
        _handle_order_error(error)


@router.patch("/orders/{order_id}/cancel")
def cancel_order_api(
    order_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    주문을 취소한다.
    """

    try:
        order = cancel_order(
            organization_id=organization_id,
            order_id=order_id,
        )

        return {
            "id": order["id"],
            "order_status": order["order_status"],
            "delivery_status": order["delivery_status"],
            "message": "주문이 취소되었습니다.",
            "order": order,
        }

    except Exception as error:
        _handle_order_error(error)


@router.patch("/orders/{order_id}/delivery-info")
def update_delivery_info_api(
    order_id: str,
    request: DeliveryInfoUpdateRequest,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    택배사, 송장번호, 배송 상태를 수정한다.
    """

    try:
        order = update_delivery_info(
            organization_id=organization_id,
            order_id=order_id,
            courier_name=request.courier_name,
            tracking_number=request.tracking_number,
            delivery_status=request.delivery_status,
        )

        return {
            "id": order["id"],
            "order_status": order["order_status"],
            "delivery_status": order["delivery_status"],
            "message": "배송 정보가 수정되었습니다.",
            "order": order,
        }

    except Exception as error:
        _handle_order_error(error)


@router.patch("/orders/{order_id}/ship")
def mark_order_shipped_api(
    order_id: str,
    request: ShipOrderRequest,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    주문을 배송 중 상태로 변경한다.
    """

    try:
        order = mark_order_shipped(
            organization_id=organization_id,
            order_id=order_id,
            courier_name=request.courier_name,
            tracking_number=request.tracking_number,
        )

        return {
            "id": order["id"],
            "order_status": order["order_status"],
            "delivery_status": order["delivery_status"],
            "message": "주문이 배송 중 상태로 변경되었습니다.",
            "order": order,
        }

    except Exception as error:
        _handle_order_error(error)


@router.patch("/orders/{order_id}/deliver")
def mark_order_delivered_api(
    order_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    주문을 배송 완료 상태로 변경한다.
    """

    try:
        order = mark_order_delivered(
            organization_id=organization_id,
            order_id=order_id,
        )

        return {
            "id": order["id"],
            "order_status": order["order_status"],
            "delivery_status": order["delivery_status"],
            "message": "주문이 배송 완료 상태로 변경되었습니다.",
            "order": order,
        }

    except Exception as error:
        _handle_order_error(error)