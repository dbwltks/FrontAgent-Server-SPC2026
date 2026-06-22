import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.core.config import settings
from app.rag.indexer import index_text
from app.rag.text_extractor import extract_text_from_file
from app.repositories.knowledge_repo import (
    list_knowledge_sources,
    get_knowledge_source,
    update_knowledge_source,
    delete_knowledge_source,
    list_knowledge_chunks,
)


router = APIRouter(tags=["Knowledge"])
logger = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 1024 * 1024
KNOWLEDGE_ERROR_MESSAGE = "Knowledge processing failed"


class KnowledgeCreateRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")
    title: str = Field(..., example="가격표")
    content: str = Field(..., example="기본 상담은 50,000원입니다.")
    folder_id: str | None = None


class KnowledgeUpdateRequest(BaseModel):
    title: str | None = None
    is_referenced: bool | None = None
    status: str | None = None


@router.post("/knowledge")
def create_knowledge(req: KnowledgeCreateRequest):
    try:
        result = index_text(
            organization_id=req.organization_id,
            title=req.title,
            text=req.content,
            folder_id=req.folder_id,
            source_type="text",
        )

        return result

    except Exception:
        logger.exception("knowledge indexing failed")
        raise HTTPException(
            status_code=500,
            detail=KNOWLEDGE_ERROR_MESSAGE,
        )


@router.post("/knowledge/upload")
async def upload_knowledge(
    organization_id: str = Form(...),
    folder_id: str | None = Form(None),
    file: UploadFile = File(...),
):
    temp_file_path = None

    try:
        original_file_name = file.filename or "uploaded_file"
        suffix = Path(original_file_name).suffix.lower()

        allowed_extensions = [".pdf", ".txt", ".md", ".csv", ".xlsx", ".xls"]

        if suffix not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {suffix}"
            )

        uploaded_bytes = 0

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file_path = temp_file.name

            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                uploaded_bytes += len(chunk)

                if uploaded_bytes > settings.knowledge_upload_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Uploaded file is too large",
                    )

                temp_file.write(chunk)

        extracted_text = await asyncio.to_thread(
            extract_text_from_file,
            file_path=temp_file_path,
            file_name=original_file_name,
        )

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text could be extracted from this file."
            )

        result = await asyncio.to_thread(
            index_text,
            organization_id=organization_id,
            title=original_file_name,
            text=extracted_text,
            folder_id=folder_id,
            source_type=suffix.replace(".", ""),
            file_name=original_file_name,
            mime_type=file.content_type,
        )

        return {
            "file_name": original_file_name,
            "mime_type": file.content_type,
            "text_length": len(extracted_text),
            **result,
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception("knowledge file upload processing failed")
        raise HTTPException(
            status_code=500,
            detail=KNOWLEDGE_ERROR_MESSAGE,
        )

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

        await file.close()


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


@router.patch("/knowledge/{source_id}")
def patch_knowledge_source(
    source_id: str,
    organization_id: str,
    req: KnowledgeUpdateRequest,
):
    update_data = {
        key: value
        for key, value in req.model_dump(exclude_unset=True).items()
        if value is not None or isinstance(value, bool)
    }

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No fields to update",
        )

    updated = update_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
        data=update_data,
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Knowledge source not found",
        )

    return updated


@router.delete("/knowledge/{source_id}")
def delete_knowledge(
    source_id: str,
    organization_id: str,
):
    deleted = delete_knowledge_source(
        organization_id=organization_id,
        source_id=source_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Knowledge source not found",
        )

    return {
        "source_id": source_id,
        "deleted": True,
    }
