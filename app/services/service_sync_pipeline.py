import logging

from app.repositories.service_repo import (
    mark_source_services_stale,
    sync_extracted_service_to_pending,
)
from app.services.service_extractor import extract_services_from_knowledge_text
from app.repositories.knowledge_repo import get_knowledge_source, list_knowledge_chunks
from app.services.service_catalog_extractor import extract_service_catalog_from_text
from app.repositories.service_catalog_repo import sync_service_catalog_to_tables


logger = logging.getLogger(__name__)


def build_knowledge_content_from_chunks(chunks: list[dict]) -> str:
    """
    knowledge_chunks를 AI 추출용 본문으로 합친다.
    """
    contents = []

    for chunk in chunks:
        content = str(chunk.get("content") or "").strip()

        if content:
            contents.append(content)

    return "\n\n".join(contents)


async def extract_and_sync_service_catalog_from_knowledge(
    *,
    organization_id: str,
    knowledge_source_id: str,
) -> dict:
    """
    지식 문서에서 대분류 서비스, 세부 상품, 옵션을 추출하고
    services / service_items / service_item_options 로 저장한다.
    """

    source = get_knowledge_source(
        organization_id=organization_id,
        source_id=knowledge_source_id,
    )

    if not source:
        raise ValueError("Knowledge source not found")

    chunks = list_knowledge_chunks(
        organization_id=organization_id,
        source_id=knowledge_source_id,
    )

    if not chunks:
        raise ValueError("Knowledge chunks not found")

    text = "\n\n".join(
        str(chunk.get("content") or "")
        for chunk in chunks
        if chunk.get("content")
    )

    if not text.strip():
        raise ValueError("Knowledge text is empty")

    catalog = await extract_service_catalog_from_text(
        title=source.get("title") or source.get("file_name") or "서비스 문서",
        text=text,
    )

    sync_result = sync_service_catalog_to_tables(
        organization_id=organization_id,
        knowledge_source_id=knowledge_source_id,
        catalog=catalog,
    )

    return {
        "organization_id": organization_id,
        "knowledge_source_id": knowledge_source_id,
        "source_title": source.get("title"),
        "catalog": catalog,
        "sync_result": sync_result,
    }

async def extract_and_sync_services_from_knowledge(
    *,
    organization_id: str,
    knowledge_source_id: str,
) -> dict:
    """
    특정 지식 source에서 서비스 정보를 추출하고 DB에 반영한다.

    우선순위:
    1. 카탈로그 추출 성공 시
       - services: 대분류 서비스
       - service_items: 실제 예약 상품
       - service_item_options: 상품 옵션

    2. 카탈로그 추출 실패 시
       - 기존 방식대로 services 테이블에 pending 서비스 후보 저장
    """

    source = get_knowledge_source(
        organization_id=organization_id,
        source_id=knowledge_source_id,
    )

    if not source:
        raise ValueError("Knowledge source not found")

    chunks = list_knowledge_chunks(
        organization_id=organization_id,
        source_id=knowledge_source_id,
    )

    content = build_knowledge_content_from_chunks(chunks)

    title = (
        source.get("title")
        or source.get("source_title")
        or source.get("file_name")
        or "서비스 문서"
    )

    if not content.strip():
        return {
            "organization_id": organization_id,
            "knowledge_source_id": knowledge_source_id,
            "source_title": title,
            "mode": "empty",
            "extracted_count": 0,
            "synced_count": 0,
            "stale_count": 0,
            "items": [],
            "stale_items": [],
            "catalog_sync": None,
        }

    catalog_error = None
    catalog_preview = None

    try:
        catalog = await extract_service_catalog_from_text(
            title=title,
            text=content,
        )

        catalog_preview = catalog
        catalog_items = catalog.get("items") or []

        if isinstance(catalog_items, list) and len(catalog_items) > 0:
            try:
                catalog_sync = sync_service_catalog_to_tables(
                    organization_id=organization_id,
                    knowledge_source_id=knowledge_source_id,
                    catalog=catalog,
                )
            except Exception as e:
                catalog_error = repr(e)

                logger.warning(
                    "Failed to sync service catalog. Do not fallback to flat services: organization_id=%s, knowledge_source_id=%s, error=%s",
                    organization_id,
                    knowledge_source_id,
                    catalog_error,
                    exc_info=True,
                )

                return {
                    "organization_id": organization_id,
                    "knowledge_source_id": knowledge_source_id,
                    "source_title": title,
                    "mode": "catalog_failed",
                    "extracted_count": len(catalog_items),
                    "synced_count": 0,
                    "options_count": 0,
                    "stale_count": 0,
                    "items": [],
                    "stale_items": [],
                    "catalog_sync": None,
                    "catalog_error": catalog_error,
                    "catalog_preview": catalog_preview,
                }

            service_name = (
                catalog_sync.get("service_name")
                or catalog.get("service_name")
            )

            extracted_names = []
            if service_name:
                extracted_names.append(str(service_name).strip())

            stale_services = mark_source_services_stale(
                organization_id=organization_id,
                source_id=knowledge_source_id,
                extracted_names=extracted_names,
            )

            return {
                "organization_id": organization_id,
                "knowledge_source_id": knowledge_source_id,
                "source_title": title,
                "mode": "catalog",
                "extracted_count": len(catalog_items),
                "synced_count": catalog_sync.get("items_count", 0),
                "options_count": catalog_sync.get("options_count", 0),
                "stale_count": len(stale_services),
                "items": [catalog_sync.get("service")]
                if catalog_sync.get("service")
                else [],
                "catalog": catalog,
                "catalog_sync": catalog_sync,
                "catalog_items": catalog_sync.get("items", []),
                "catalog_options": catalog_sync.get("options", []),
                "stale_items": stale_services,
            }

    except Exception as e:
        catalog_error = repr(e)

        logger.warning(
            "Failed to extract service catalog. Fallback to flat service extraction: organization_id=%s, knowledge_source_id=%s, error=%s",
            organization_id,
            knowledge_source_id,
            catalog_error,
            exc_info=True,
        )

    # ------------------------------------------------------------
    # fallback: 기존 방식
    # 지식에서 서비스 후보를 추출하고 services 테이블에 pending 저장
    # ------------------------------------------------------------
    extracted_services = await extract_services_from_knowledge_text(
        organization_id=organization_id,
        title=title,
        content=content,
    )

    synced_services = []

    for extracted_service in extracted_services:
        try:
            synced = sync_extracted_service_to_pending(
                organization_id=organization_id,
                knowledge_source_id=knowledge_source_id,
                extracted_service=extracted_service,
            )
            synced_services.append(synced)
        except Exception:
            logger.warning(
                "Failed to sync extracted service: organization_id=%s, knowledge_source_id=%s, service=%s",
                organization_id,
                knowledge_source_id,
                extracted_service.get("name"),
                exc_info=True,
            )

    extracted_names = [
        str(item.get("name")).strip()
        for item in extracted_services
        if item.get("name")
    ]

    stale_services = mark_source_services_stale(
        organization_id=organization_id,
        source_id=knowledge_source_id,
        extracted_names=extracted_names,
    )

    return {
        "organization_id": organization_id,
        "knowledge_source_id": knowledge_source_id,
        "source_title": title,
        "mode": "flat_services",
        "extracted_count": len(extracted_services),
        "synced_count": len(synced_services),
        "stale_count": len(stale_services),
        "items": synced_services,
        "stale_items": stale_services,
        "catalog_sync": None,
        "catalog_error": catalog_error,
        "catalog_preview": catalog_preview,
    }