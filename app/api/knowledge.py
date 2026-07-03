import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from uuid import uuid4
import httpx

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from app.services.service_sync_pipeline import extract_and_sync_services_from_knowledge

from app.core.config import settings
from app.rag.indexer import create_knowledge_source, index_text, update_source_status
from app.rag.text_extractor import extract_text_from_file, extract_text_from_url, extract_chunks_from_file
from app.repositories.knowledge_repo import (
    list_knowledge_sources,
    get_knowledge_source,
    get_knowledge_source_by_checksum,
    update_knowledge_source,
    delete_knowledge_source,
    list_knowledge_chunks,
)
from app.repositories.knowledge_storage import (
    build_knowledge_storage_path,
    content_type_for_file,
    create_knowledge_download_url,
    delete_knowledge_original,
    upload_knowledge_original,
)


router = APIRouter(tags=["Knowledge"])
logger = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 1024 * 1024
KNOWLEDGE_ERROR_MESSAGE = "Knowledge processing failed"


class KnowledgeCreateRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    title: str = Field(..., example="화장실 청소 안내")
    content: str = Field(
        ...,
        example="화장실 청소는 세면대, 변기, 바닥, 배수구를 청소하는 서비스입니다.",
    )
    folder_id: str | None = None

    # 지식 등록 후 서비스 후보 자동 추출 여부
    auto_extract_services: bool = True


class KnowledgeUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None

    is_referenced: bool | None = None
    status: str | None = None

    # 본문 수정 후 서비스 후보 재추출 여부
    auto_extract_services: bool = True


@router.post("/knowledge")
async def create_knowledge(req: KnowledgeCreateRequest):
    try:
        result = await asyncio.to_thread(
            index_text,
            organization_id=req.organization_id,
            title=req.title,
            text=req.content,
            folder_id=req.folder_id,
            source_type="text",
        )

        source_id = result.get("source_id") or result.get("id")

        service_sync = await _maybe_extract_services_from_knowledge(
            organization_id=req.organization_id,
            source_id=source_id,
            enabled=req.auto_extract_services,
        )

        return {
            **result,
            "service_sync": service_sync,
        }

    except Exception:
        logger.exception("knowledge indexing failed")
        raise HTTPException(
            status_code=500,
            detail=KNOWLEDGE_ERROR_MESSAGE,
        )

class KnowledgeUrlRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="00000000-0000-0000-0000-000000000000",
    )
    url: str = Field(..., example="https://example.com/faq")
    auto_extract_services: bool = True


@router.post("/knowledge/url")
async def create_knowledge_from_url(req: KnowledgeUrlRequest):
    try:
        # httpx 네트워크 호출은 블로킹이라 to_thread로 돌린다
        text = await asyncio.to_thread(extract_text_from_url, req.url)

        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text could be extracted from this URL.",
            )

        result = await asyncio.to_thread(
            index_text,
            organization_id=req.organization_id,
            title=req.url,            # v1은 URL을 제목으로
            text=text,
            source_type="url",
        )

        source_id = result.get("source_id") or result.get("id")

        service_sync = await _maybe_extract_services_from_knowledge(
            organization_id=req.organization_id,
            source_id=source_id,
            enabled=req.auto_extract_services,
        )

        return {
            **result,
            "service_sync": service_sync,
        }

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"URL을 가져올 수 없습니다 (상태 {exc.response.status_code}).",
    )

    except HTTPException:
        raise
    except Exception:
        logger.exception("knowledge url indexing failed")
        raise HTTPException(
            status_code=500,
            detail=KNOWLEDGE_ERROR_MESSAGE,
        )
    
async def _background_index(
    temp_file_path: str,
    original_file_name: str,
    suffix: str,
    organization_id: str,
    folder_id: str | None,
    source_id: str,
    storage_path: str,
    mime_type: str,
    uploaded_bytes: int,
):
    """업로드 완료 후 텍스트 추출 → 인덱싱을 백그라운드에서 실행."""
    try:
        await asyncio.to_thread(update_source_status, source_id, "extracting")
        extracted_text = await asyncio.to_thread(
            extract_text_from_file,
            file_path=temp_file_path,
            file_name=original_file_name,
        )
        if not extracted_text.strip():
            await asyncio.to_thread(update_source_status, source_id, "failed")
            return

        await asyncio.to_thread(
            index_text,
            organization_id=organization_id,
            title=original_file_name,
            text=extracted_text,
            folder_id=folder_id,
            source_type=suffix.replace(".", ""),
            file_name=original_file_name,
            mime_type=mime_type,
            source_id=source_id,
        )

        await _maybe_extract_services_from_knowledge(
            organization_id=organization_id,
            source_id=source_id,
            enabled=True,
        )
    except Exception:
        logger.exception("background indexing failed: source_id=%s", source_id)
        await asyncio.to_thread(update_source_status, source_id, "failed")
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)


@router.post("/knowledge/upload")
async def upload_knowledge(
    background_tasks: BackgroundTasks,
    organization_id: str = Form(...),
    folder_id: str | None = Form(None),
    file: UploadFile = File(...),
):
    temp_file_path = None
    source_id = None
    source_created = False

    try:
        original_file_name = file.filename or "uploaded_file"
        suffix = Path(original_file_name).suffix.lower()

        allowed_extensions = [".pdf", ".txt", ".md", ".xlsx", ".xls"]

        if suffix not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {suffix}"
            )

        uploaded_bytes = 0
        checksum = hashlib.sha256()

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file_path = temp_file.name

            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                uploaded_bytes += len(chunk)
                checksum.update(chunk)

                if uploaded_bytes > settings.knowledge_upload_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Uploaded file is too large",
                    )

                temp_file.write(chunk)

        checksum_sha256 = checksum.hexdigest()

        existing_source = await asyncio.to_thread(
            get_knowledge_source_by_checksum,
            organization_id,
            checksum_sha256,
        )

        if existing_source:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "이미 등록된 지식 파일입니다.",
                    "source_id": existing_source.get("id"),
                    "title": existing_source.get("title"),
                    "file_name": existing_source.get("file_name"),
                    "status": existing_source.get("status"),
                },
            )


        source_id = str(uuid4())
        mime_type = content_type_for_file(original_file_name, file.content_type)
        storage_path = build_knowledge_storage_path(
            organization_id=organization_id,
            source_id=source_id,
            file_name=original_file_name,
        )

        await asyncio.to_thread(
            create_knowledge_source,
            organization_id=organization_id,
            title=original_file_name,
            source_type=suffix.replace(".", ""),
            folder_id=folder_id,
            file_name=original_file_name,
            mime_type=mime_type,
            source_id=source_id,
            storage_bucket=settings.knowledge_storage_bucket,
            storage_path=storage_path,
            file_size=uploaded_bytes,
            checksum_sha256=checksum_sha256,
            status="uploading",
        )
        source_created = True

        await asyncio.to_thread(
            upload_knowledge_original,
            local_file_path=temp_file_path,
            storage_path=storage_path,
            content_type=mime_type,
        )

        # Storage 업로드 완료 → 인덱싱은 백그라운드로
        await asyncio.to_thread(update_source_status, source_id, "processing")
        background_tasks.add_task(
            _background_index,
            temp_file_path=temp_file_path,
            original_file_name=original_file_name,
            suffix=suffix,
            organization_id=organization_id,
            folder_id=folder_id,
            source_id=source_id,
            storage_path=storage_path,
            mime_type=mime_type,
            uploaded_bytes=uploaded_bytes,
        )
        temp_file_path = None  # 백그라운드에서 정리하므로 finally에서 삭제 안 함

        return {
            "source_id": source_id,
            "file_name": original_file_name,
            "mime_type": mime_type,
            "file_size": uploaded_bytes,
            "status": "processing",
        }

    except HTTPException:
        if source_created and source_id:
            await asyncio.to_thread(_mark_source_failed, source_id)
        raise

    except Exception:
        if source_created and source_id:
            await asyncio.to_thread(_mark_source_failed, source_id)
        logger.exception("knowledge file upload processing failed")
        raise HTTPException(
            status_code=500,
            detail=KNOWLEDGE_ERROR_MESSAGE,
        )

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

        await file.close()


async def _maybe_extract_services_from_knowledge(
    *,
    organization_id: str,
    source_id: str | None,
    enabled: bool,
) -> dict | None:
    """
    지식 저장/수정 후 서비스 후보를 추출한다.

    서비스 추출 실패가 지식 저장 실패로 이어지면 안 되므로,
    예외는 잡아서 sync_error 형태로 반환한다.
    """
    if not enabled or not source_id:
        return None

    try:
        return await extract_and_sync_services_from_knowledge(
            organization_id=organization_id,
            knowledge_source_id=source_id,
        )
    except Exception as exc:
        logger.warning(
            "service extraction after knowledge indexing failed: organization_id=%s, source_id=%s",
            organization_id,
            source_id,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": str(exc),
        }


def _mark_source_failed(source_id: str) -> None:
    try:
        update_source_status(source_id, "failed")
    except Exception:
        logger.warning("failed to mark knowledge source as failed", exc_info=True)


@router.get("/knowledge")
def get_knowledge_list(
    organization_id: str,
    folder_id: str | None = None,
):
    sources = list_knowledge_sources(
        organization_id=organization_id,
        folder_id=folder_id,
    )

    return {
        "organization_id": organization_id,
        "folder_id": folder_id,
        "count": len(sources),
        "items": sources,
    }


@router.get("/knowledge/{source_id}/chunks")
def get_knowledge_chunks(source_id: str, organization_id: str):
    chunks = list_knowledge_chunks(
        organization_id=organization_id,
        source_id=source_id,
    )

    return {
        "organization_id": organization_id,
        "source_id": source_id,
        "count": len(chunks),
        "items": chunks,
    }


@router.get("/knowledge/{source_id}")
def get_knowledge_detail(source_id: str, organization_id: str):
    source = get_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    if not source:
        raise HTTPException(
            status_code=404,
            detail="Knowledge source not found",
        )

    return source


@router.get("/knowledge/{source_id}/download-url")
def get_knowledge_download_url(source_id: str, organization_id: str):
    source = get_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    if not source:
        raise HTTPException(status_code=404, detail="Knowledge source not found")

    storage_bucket = source.get("storage_bucket")
    storage_path = source.get("storage_path")

    if not storage_bucket or not storage_path:
        raise HTTPException(status_code=404, detail="Original file not found")

    try:
        signed_url = create_knowledge_download_url(storage_bucket, storage_path)
    except Exception:
        logger.exception("knowledge original signed URL creation failed")
        raise HTTPException(status_code=500, detail=KNOWLEDGE_ERROR_MESSAGE)

    return {
        "source_id": source_id,
        "file_name": source.get("file_name"),
        "expires_in": 300,
        "url": signed_url,
    }


@router.patch("/knowledge/{source_id}")
async def patch_knowledge_source(
    source_id: str,
    organization_id: str,
    req: KnowledgeUpdateRequest,
):
    source = get_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    if not source:
        raise HTTPException(
            status_code=404,
            detail="Knowledge source not found",
        )

    update_data = {
        key: value
        for key, value in req.model_dump(
            exclude_unset=True,
            exclude={"content", "auto_extract_services"},
        ).items()
        if value is not None or isinstance(value, bool)
    }

    content_changed = req.content is not None

    if not update_data and not content_changed:
        raise HTTPException(
            status_code=400,
            detail="No fields to update",
        )

    updated_source = source

    if update_data:
        updated_source = update_knowledge_source(
            organization_id=organization_id,
            source_id=source_id,
            data=update_data,
        )

        if not updated_source:
            raise HTTPException(
                status_code=404,
                detail="Knowledge source not found",
            )

    service_sync = None

    if content_changed:
        new_title = (
            req.title
            or updated_source.get("title")
            or source.get("title")
            or source.get("file_name")
            or "지식 문서"
        )

        await asyncio.to_thread(
            update_source_status,
            source_id,
            "indexing",
        )

        await asyncio.to_thread(
            delete_knowledge_chunks,
            organization_id=organization_id,
            source_id=source_id,
        )

        reindex_result = await asyncio.to_thread(
            index_text,
            organization_id=organization_id,
            title=new_title,
            text=req.content or "",
            folder_id=updated_source.get("folder_id") or source.get("folder_id"),
            source_type=updated_source.get("source_type") or source.get("source_type") or "text",
            file_name=updated_source.get("file_name") or source.get("file_name"),
            mime_type=updated_source.get("mime_type") or source.get("mime_type"),
            source_id=source_id,
        )

        service_sync = await _maybe_extract_services_from_knowledge(
            organization_id=organization_id,
            source_id=source_id,
            enabled=req.auto_extract_services,
        )

        updated_source = get_knowledge_source(
            organization_id=organization_id,
            source_id=source_id,
        )

        return {
            "knowledge": updated_source,
            "reindex": reindex_result,
            "service_sync": service_sync,
        }

    return {
        "knowledge": updated_source,
        "service_sync": service_sync,
    }


@router.post("/knowledge/{source_id}/reindex")
async def reindex_knowledge(
    source_id: str,
    organization_id: str,
):
    """
    기존 knowledge source의 원본 파일(Storage)을 다시 추출·임베딩한다.
    텍스트 소스는 현재 content를 그대로 재인덱싱하고,
    파일 소스(pdf/csv/txt 등)는 Storage에서 원본을 다운로드해 재처리한다.
    """
    from app.repositories.knowledge_repo import delete_knowledge_chunks
    from app.core.config import settings as app_settings

    source = get_knowledge_source(organization_id=organization_id, source_id=source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge source not found")

    storage_path = source.get("storage_path")
    source_type = source.get("source_type", "text")
    title = source.get("title") or source.get("file_name") or "지식 문서"
    file_name = source.get("file_name")
    folder_id = source.get("folder_id")

    await asyncio.to_thread(update_source_status, source_id, "indexing")

    try:
        if storage_path:
            # 파일 소스: Storage에서 원본 다운로드 후 텍스트 추출
            from app.core.db import supabase as _supabase
            bucket = app_settings.knowledge_storage_bucket
            raw = await asyncio.to_thread(
                lambda: _supabase.storage.from_(bucket).download(storage_path)
            )
            suffix = f".{source_type}" if not source_type.startswith(".") else source_type
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw)
                tmp_path = tmp.name
            try:
                fname = file_name or title
                # 행별 데이터(Excel/CSV)는 행당 1 chunk로 저장 — overlap 청킹 불필요
                pre_chunked = await asyncio.to_thread(extract_chunks_from_file, tmp_path, fname)
                if pre_chunked is None:
                    extracted_text = await asyncio.to_thread(extract_text_from_file, tmp_path, fname)
                else:
                    extracted_text = None
            finally:
                os.unlink(tmp_path)
        else:
            # 텍스트 소스: DB에 저장된 chunk 내용을 이어붙여 재인덱싱
            chunks = await asyncio.to_thread(
                list_knowledge_chunks,
                organization_id=organization_id,
                source_id=source_id,
            )
            extracted_text = "\n\n".join(c.get("content", "") for c in chunks)

        if pre_chunked is None and not (extracted_text or "").strip():
            raise HTTPException(status_code=400, detail="재인덱싱할 텍스트가 없습니다.")

        await asyncio.to_thread(
            delete_knowledge_chunks,
            organization_id=organization_id,
            source_id=source_id,
        )

        result = await asyncio.to_thread(
            index_text,
            organization_id=organization_id,
            title=title,
            text=extracted_text or "",
            folder_id=folder_id,
            source_type=source_type,
            file_name=file_name,
            source_id=source_id,
            pre_chunked=pre_chunked,
        )

        from app.rag.retriever import clear_semantic_cache
        from app.rag.keyword_vocabulary import clear_organization_keyword_vocabulary

        await asyncio.to_thread(clear_semantic_cache, organization_id)
        clear_organization_keyword_vocabulary(organization_id)

        return {
            "source_id": source_id,
            "chunks": result.get("chunks", 0),
            "status": "indexed",
        }

    except HTTPException:
        await asyncio.to_thread(update_source_status, source_id, "failed")
        raise
    except Exception as e:
        await asyncio.to_thread(update_source_status, source_id, "failed")
        logger.exception("reindex failed: source_id=%s", source_id)
        raise HTTPException(status_code=500, detail=f"재인덱싱 실패: {e}") from e


@router.delete("/knowledge/{source_id}")
def delete_knowledge(
    source_id: str,
    organization_id: str,
):
    source = get_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    if not source:
        raise HTTPException(
            status_code=404,
            detail="Knowledge source not found",
        )

    deleted = delete_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Knowledge source not found",
        )

    storage_deleted = True
    storage_bucket = source.get("storage_bucket")
    storage_path = source.get("storage_path")

    if storage_bucket and storage_path:
        try:
            delete_knowledge_original(storage_bucket, storage_path)
        except Exception:
            storage_deleted = False
            logger.warning("knowledge original deletion failed", exc_info=True)

    return {
        "source_id": source_id,
        "deleted": True,
        "storage_deleted": storage_deleted,
    }
