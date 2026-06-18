from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories.rule_repo import (
    create_rule,
    list_rules,
    get_rule,
    update_rule,
    delete_rule,
)


router = APIRouter(
    prefix="/rules",
    tags=["Rules"],
)


def model_to_dict(model: BaseModel) -> dict:
    """
    Pydantic v1, v2 둘 다 대응하기 위한 함수.
    """

    if hasattr(model, "model_dump"):
        return model.model_dump()

    return model.dict()


class RuleCreateRequest(BaseModel):
    """
    규칙 생성 요청.

    이번 rules 구조에서는 필터, 트리거, 액션을 받지 않는다.
    규칙 이름과 지시문만 등록한다.
    """

    organization_id: str = Field(
        ...,
        example="org_test",
    )

    name: str = Field(
        ...,
        example="반말하지 않기",
    )

    instruction: str = Field(
        ...,
        example="고객에게 절대 반말하지 않고 항상 존댓말로 응답한다.",
    )

    is_active: bool = Field(
        default=True,
        example=True,
    )


class RuleUpdateRequest(BaseModel):
    """
    규칙 수정 요청.

    수정 가능한 값:
    - name
    - instruction
    - is_active
    """

    name: str | None = Field(
        default=None,
        example="상냥하게 말하기",
    )

    instruction: str | None = Field(
        default=None,
        example="고객에게 항상 친절하고 부드러운 말투로 응답한다.",
    )

    is_active: bool | None = Field(
        default=None,
        example=True,
    )


@router.post("")
def create_rule_api(req: RuleCreateRequest):
    """
    규칙을 생성한다.
    """

    data = model_to_dict(req)

    created = create_rule(data)

    if not created:
        raise HTTPException(
            status_code=500,
            detail="Rule creation failed",
        )

    return created


@router.get("")
def list_rules_api(organization_id: str):
    """
    특정 조직의 규칙 목록을 조회한다.
    """

    rules = list_rules(organization_id)

    return {
        "organization_id": organization_id,
        "count": len(rules),
        "items": rules,
    }


@router.get("/{rule_id}")
def get_rule_api(
    rule_id: str,
    organization_id: str,
):
    """
    특정 규칙 하나를 조회한다.
    """

    rule = get_rule(
        organization_id=organization_id,
        rule_id=rule_id,
    )

    if not rule:
        raise HTTPException(
            status_code=404,
            detail="Rule not found",
        )

    return rule


@router.patch("/{rule_id}")
def update_rule_api(
    rule_id: str,
    organization_id: str,
    req: RuleUpdateRequest,
):
    """
    특정 규칙을 수정한다.
    """

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

    updated = update_rule(
        organization_id=organization_id,
        rule_id=rule_id,
        data=update_data,
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Rule not found",
        )

    return updated


@router.delete("/{rule_id}")
def delete_rule_api(
    rule_id: str,
    organization_id: str,
):
    """
    특정 규칙을 삭제한다.
    """

    deleted = delete_rule(
        organization_id=organization_id,
        rule_id=rule_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Rule not found",
        )

    return {
        "rule_id": rule_id,
        "deleted": True,
    }