from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories.rule_repo import (
    create_rule,
    list_rules,
    get_rule,
    update_rule,
    delete_rule,
    reset_builtin_rule,
    update_builtin_rule,
)

BUILTIN_RULE_ID_PREFIX = "builtin:"


router = APIRouter(
    prefix="/rules",
    tags=["Rules"],
)

# 규칙은 응답 생성 시 매번 전체가 LLM 프롬프트에 그대로 주입된다.
# 너무 많으면 지시가 희석되고 토큰 비용도 늘어나므로 조직당 개수를 제한한다.
MAX_RULES_PER_ORGANIZATION = 10


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

    existing_count = len(list_rules(req.organization_id))
    if existing_count >= MAX_RULES_PER_ORGANIZATION:
        raise HTTPException(
            status_code=400,
            detail=f"규칙은 조직당 최대 {MAX_RULES_PER_ORGANIZATION}개까지 등록할 수 있습니다.",
        )

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

    count는 빌트인 규칙 3개를 포함한 전체 개수다(MAX_RULES_PER_ORGANIZATION 한도와 동일 기준).
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

    rule_id가 빌트인 규칙(`builtin:`로 시작)이면 오버라이드로 저장한다.
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

    if rule_id.startswith(BUILTIN_RULE_ID_PREFIX):
        builtin_key = rule_id[len(BUILTIN_RULE_ID_PREFIX):]
        updated = update_builtin_rule(
            organization_id=organization_id,
            builtin_key=builtin_key,
            data=update_data,
        )
    else:
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


@router.post("/{rule_id}/reset")
def reset_builtin_rule_api(
    rule_id: str,
    organization_id: str,
):
    """
    빌트인 규칙을 코드 기본값으로 되돌린다. 커스텀 규칙에는 사용할 수 없다.
    """

    if not rule_id.startswith(BUILTIN_RULE_ID_PREFIX):
        raise HTTPException(
            status_code=400,
            detail="커스텀 규칙은 기본값으로 되돌릴 수 없습니다.",
        )

    builtin_key = rule_id[len(BUILTIN_RULE_ID_PREFIX):]
    reset = reset_builtin_rule(organization_id=organization_id, builtin_key=builtin_key)

    if not reset:
        raise HTTPException(
            status_code=404,
            detail="Rule not found",
        )

    return reset


@router.delete("/{rule_id}")
def delete_rule_api(
    rule_id: str,
    organization_id: str,
):
    """
    특정 규칙을 삭제한다. 빌트인 규칙은 삭제할 수 없다.
    """

    if rule_id.startswith(BUILTIN_RULE_ID_PREFIX):
        raise HTTPException(
            status_code=400,
            detail="기본 제공 규칙은 삭제할 수 없습니다. 비활성화하거나 기본값으로 되돌리세요.",
        )

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