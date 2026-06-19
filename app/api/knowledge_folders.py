from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories.knowledge_folder_repo import (
    create_knowledge_folder,
    list_knowledge_folders,
    get_knowledge_folder,
    update_knowledge_folder,
    delete_knowledge_folder,
)


router = APIRouter(
    prefix="/knowledge/folders",
    tags=["Knowledge Folders"],
)


def model_to_dict(model: BaseModel) -> dict:
    """
    Pydantic v1, v2 둘 다 대응하기 위한 함수.
    """
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class KnowledgeFolderCreateRequest(BaseModel):
    organization_id: str = Field(
        ...,
        example="org_test",
    )
    name: str = Field(
        ...,
        example="FAQ",
    )
    description: str | None = Field(
        default=None,
        example="자주 묻는 질문 모음",
    )


class KnowledgeFolderUpdateRequest(BaseModel):
    name: str | None = Field(
        default=None,
        example="가격/서비스",
    )
    description: str | None = Field(
        default=None,
        example="서비스 가격표와 상품 설명",
    )


@router.post("")
def create_knowledge_folder_api(req: KnowledgeFolderCreateRequest):
    data = model_to_dict(req)

    created = create_knowledge_folder(data)

    if not created:
        raise HTTPException(
            status_code=500,
            detail="Knowledge folder creation failed",
        )

    return created


@router.get("")
def list_knowledge_folders_api(organization_id: str):
    folders = list_knowledge_folders(organization_id)

    return {
        "organization_id": organization_id,
        "count": len(folders),
        "items": folders,
    }


@router.get("/{folder_id}")
def get_knowledge_folder_api(
    folder_id: str,
    organization_id: str,
):
    folder = get_knowledge_folder(
        organization_id=organization_id,
        folder_id=folder_id,
    )

    if not folder:
        raise HTTPException(
            status_code=404,
            detail="Knowledge folder not found",
        )

    return folder


@router.patch("/{folder_id}")
def update_knowledge_folder_api(
    folder_id: str,
    organization_id: str,
    req: KnowledgeFolderUpdateRequest,
):
    raw_data = model_to_dict(req)

    update_data = {
        key: value
        for key, value in raw_data.items()
        if value is not None
    }

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="No fields to update",
        )

    updated = update_knowledge_folder(
        organization_id=organization_id,
        folder_id=folder_id,
        data=update_data,
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Knowledge folder not found",
        )

    return updated


@router.delete("/{folder_id}")
def delete_knowledge_folder_api(
    folder_id: str,
    organization_id: str,
):
    deleted = delete_knowledge_folder(
        organization_id=organization_id,
        folder_id=folder_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Knowledge folder not found",
        )

    return {
        "folder_id": folder_id,
        "deleted": True,
    }