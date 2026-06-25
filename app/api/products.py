from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.repositories.product_repo import (
    ProductNotFoundError,
    ProductRepoError,
    ProductStockError,
    check_product_stock,
    get_product,
    list_products,
    search_products,
)

router = APIRouter(tags=["Products"])


def _handle_product_error(error: Exception) -> None:
    print("Product API ERROR:", type(error).__name__, str(error))

    if isinstance(error, ProductNotFoundError):
        raise HTTPException(status_code=404, detail=str(error))

    if isinstance(error, ProductStockError):
        raise HTTPException(status_code=409, detail=str(error))

    if isinstance(error, ProductRepoError):
        raise HTTPException(status_code=400, detail=str(error))

    raise HTTPException(status_code=500, detail="Product API error")


@router.get("/products")
def list_products_api(
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    category: str | None = Query(default=None, examples=["의류"]),
    keyword: str | None = Query(default=None, examples=["티셔츠"]),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """
    상품 목록을 조회한다.

    keyword가 있으면 상품명/설명/카테고리/sku 기준으로 검색한다.
    category가 있으면 카테고리 필터를 적용한다.
    """

    try:
        if keyword:
            items = search_products(
                organization_id=organization_id,
                keyword=keyword,
                category=category,
                include_inactive=include_inactive,
                limit=limit,
            )
        else:
            items = list_products(
                organization_id=organization_id,
                category=category,
                include_inactive=include_inactive,
                limit=limit,
            )

        return {
            "organization_id": organization_id,
            "count": len(items),
            "items": items,
        }

    except Exception as error:
        _handle_product_error(error)


@router.get("/products/{product_id}")
def get_product_api(
    product_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
) -> dict[str, Any]:
    """
    상품 상세 정보를 조회한다.
    """

    try:
        product = get_product(
            organization_id=organization_id,
            product_id=product_id,
        )

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        return product

    except HTTPException:
        raise

    except Exception as error:
        _handle_product_error(error)


@router.get("/products/{product_id}/stock")
def check_product_stock_api(
    product_id: str,
    organization_id: str = Query(
        ...,
        examples=["00000000-0000-0000-0000-000000000000"],
    ),
    quantity: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    """
    상품 재고를 확인한다.

    주문 생성 전 테스트용으로 사용한다.
    """

    try:
        return check_product_stock(
            organization_id=organization_id,
            product_id=product_id,
            quantity=quantity,
        )

    except Exception as error:
        _handle_product_error(error)