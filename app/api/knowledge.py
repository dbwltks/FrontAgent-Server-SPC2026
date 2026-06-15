import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

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

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Knowledge indexing failed: {str(e)}"
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

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name

        extracted_text = extract_text_from_file(
            file_path=temp_file_path,
            file_name=original_file_name,
        )

        if not extracted_text.strip():
            raise HTTPException(
                status_code=400,
                detail="No text could be extracted from this file."
            )

        result = index_text(
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

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"File upload indexing failed: {str(e)}"
        )

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)


@router.get("/knowledge")
def get_knowledge_list(organization_id: str):
    sources = list_knowledge_sources(organization_id)

    return {
        "organization_id": organization_id,
        "count": len(sources),
        "items": sources,
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


@router.patch("/knowledge/{source_id}")
def patch_knowledge_source(
    source_id: str,
    organization_id: str,
    req: KnowledgeUpdateRequest,
):
    update_data = {
        key: value
        for key, value in req.model_dump().items()
        if value is not None
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