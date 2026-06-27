import logging

from app.repositories.knowledge_repo import (
    get_knowledge_source,
    list_knowledge_chunks,
)
from app.repositories.service_repo import (
    mark_source_services_stale,
    sync_extracted_service_to_pending,
)
from app.services.service_extractor import extract_services_from_knowledge_text


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


async def extract_and_sync_services_from_knowledge(
    *,
    organization_id: str,
    knowledge_source_id: str,
) -> dict:
    """
    특정 지식 source에서 서비스 후보를 추출하고 services 테이블에 pending으로 반영한다.

    반환값:
    - extracted_count: AI가 추출한 후보 수
    - synced_count: services에 반영된 수
    - stale_count: 이번 추출에서 사라진 기존 source 기반 서비스 수
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

    if not content.strip():
        return {
            "organization_id": organization_id,
            "knowledge_source_id": knowledge_source_id,
            "source_title": source.get("title"),
            "extracted_count": 0,
            "synced_count": 0,
            "stale_count": 0,
            "items": [],
            "stale_items": [],
        }

    title = (
        source.get("title")
        or source.get("source_title")
        or source.get("file_name")
    )

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
        "extracted_count": len(extracted_services),
        "synced_count": len(synced_services),
        "stale_count": len(stale_services),
        "items": synced_services,
        "stale_items": stale_services,
    }