from __future__ import annotations

from app.core.db import supabase


class ProductRepoError(Exception):
    """상품 도메인 repository 공통 예외입니다."""


class ProductNotFoundError(ProductRepoError):
    """상품을 찾지 못했을 때 사용합니다."""


class ProductStockError(ProductRepoError):
    """상품 재고가 부족하거나 재고 확인이 불가능할 때 사용합니다."""


def list_products(
    organization_id: str,
    category: str | None = None,
    include_inactive: bool = False,
    limit: int = 50,
) -> list[dict]:
    """
    조직의 상품 목록을 조회합니다.

    기본적으로 is_active = true 상품만 조회합니다.
    category가 있으면 해당 카테고리만 조회합니다.

    예:
    - 전체 상품 조회
    - 의류 상품 조회
    - 전자기기 상품 조회
    """

    query = (
        supabase.table("products")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if not include_inactive:
        query = query.eq("is_active", True)

    if category:
        query = query.eq("category", category)

    result = query.execute()
    return result.data or []


def get_product(
    organization_id: str,
    product_id: str,
) -> dict | None:
    """
    product_id 기준으로 상품 상세 정보를 조회합니다.
    """

    result = (
        supabase.table("products")
        .select("*")
        .eq("organization_id", organization_id)
        .eq("id", product_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    return rows[0] if rows else None


def get_product_or_raise(
    organization_id: str,
    product_id: str,
) -> dict:
    """
    상품을 조회하고, 없으면 ProductNotFoundError를 발생시킵니다.

    주문 생성처럼 상품이 반드시 필요한 로직에서 사용합니다.
    """

    product = get_product(
        organization_id=organization_id,
        product_id=product_id,
    )

    if not product:
        raise ProductNotFoundError("Product not found")

    return product


def search_products(
    organization_id: str,
    keyword: str | None = None,
    category: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[dict]:
    """
    상품명, 짧은 설명, 상세 설명, 카테고리 기준으로 상품을 검색합니다.

    keyword가 없으면 list_products와 비슷하게 동작합니다.
    category가 있으면 카테고리 필터를 함께 적용합니다.
    """

    query = (
        supabase.table("products")
        .select("*")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if not include_inactive:
        query = query.eq("is_active", True)

    if category:
        query = query.eq("category", category)

    if keyword:
        safe_keyword = _escape_postgrest_search_value(keyword.strip())

        if safe_keyword:
            query = query.or_(
                ",".join(
                    [
                        f"name.ilike.%{safe_keyword}%",
                        f"short_description.ilike.%{safe_keyword}%",
                        f"description.ilike.%{safe_keyword}%",
                        f"category.ilike.%{safe_keyword}%",
                        f"sku.ilike.%{safe_keyword}%",
                    ]
                )
            )

    result = query.execute()
    return result.data or []


def check_product_stock(
    organization_id: str,
    product_id: str,
    quantity: int = 1,
) -> dict:
    """
    상품 재고를 확인합니다.

    주문 생성 전 Function Node에서 사용할 수 있습니다.

    반환 예:
    {
        "available": true,
        "product_id": "...",
        "requested_quantity": 2,
        "stock_quantity": 10,
        "product": {...}
    }
    """

    if quantity <= 0:
        raise ProductStockError("Quantity must be greater than 0")

    product = get_product_or_raise(
        organization_id=organization_id,
        product_id=product_id,
    )

    if not product.get("is_active", True):
        return {
            "available": False,
            "reason": "inactive_product",
            "product_id": product_id,
            "requested_quantity": quantity,
            "stock_quantity": product.get("stock_quantity"),
            "product": product,
        }

    stock_quantity = product.get("stock_quantity")

    if stock_quantity is None:
        return {
            "available": False,
            "reason": "stock_not_managed",
            "product_id": product_id,
            "requested_quantity": quantity,
            "stock_quantity": None,
            "product": product,
        }

    stock_quantity = int(stock_quantity)

    return {
        "available": stock_quantity >= quantity,
        "reason": None if stock_quantity >= quantity else "insufficient_stock",
        "product_id": product_id,
        "requested_quantity": quantity,
        "stock_quantity": stock_quantity,
        "product": product,
    }


def decrease_product_stock(
    organization_id: str,
    product_id: str,
    quantity: int,
) -> dict:
    """
    상품 재고를 차감합니다.

    주의:
    MVP용 단순 차감입니다.
    동시 주문까지 강하게 막으려면 나중에 DB RPC나 트랜잭션/락으로 보강해야 합니다.
    """

    stock_result = check_product_stock(
        organization_id=organization_id,
        product_id=product_id,
        quantity=quantity,
    )

    if not stock_result["available"]:
        raise ProductStockError(stock_result["reason"] or "Product stock unavailable")

    product = stock_result["product"]
    current_stock = int(product.get("stock_quantity") or 0)
    next_stock = current_stock - quantity

    result = (
        supabase.table("products")
        .update({"stock_quantity": next_stock})
        .eq("organization_id", organization_id)
        .eq("id", product_id)
        .execute()
    )

    rows = result.data or []

    if not rows:
        raise ProductRepoError("Failed to decrease product stock")

    return rows[0]


def _escape_postgrest_search_value(value: str) -> str:
    """
    PostgREST or_ 검색 문자열에 들어갈 값을 최소한으로 정리합니다.

    쉼표와 괄호는 PostgREST 필터 문법과 충돌할 수 있어서 제거합니다.
    """

    return (
        value.replace(",", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("%", "")
        .strip()
    )