from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories.rule_repo import (
    create_rule,
    list_rules,
    get_rule,
    update_rule,
    delete_rule,
    get_active_rules,
)
from app.rules.rule_engine import (
    filter_rules_by_intent,
    get_block_rules,
    get_warn_rules,
    get_handoff_rules,
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

# 룰 작성 양식
class RuleCreateRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")

    name: str = Field(..., example="에어컨 냉장고 동시 수리 제한")
    description: str | None = Field(
        default=None,
        example="에어컨과 냉장고를 동시에 수리할 수 없도록 막는 룰",
    )

    rule_type: str = Field(
        default="reservation",
        example="reservation",
    )


    # 단어가 하나만 있어도 되는지 전부 있어야 되는지
    trigger_condition: str = Field(
        default="contains_all",
        example="contains_all",
        description="always, intent, contains_any, contains_all, intent_and_contains_any, intent_and_contains_all",
    )

    instruction: str = Field(
        ...,
        example="사용자가 에어컨과 냉장고를 동시에 요청하면 예약을 진행하지 말고, 하나씩 따로 예약하도록 안내한다.",
    )

    # 검사할 단어
    filters: list[str] = Field(
        default_factory=list,
        example=["에어컨", "냉장고"],
    )

    # 경고만 할 지, 막을지, 상담원에게 넘길지
    action_type: str = Field(
        default="block",
        example="block",
        description="block, warn, handoff",
    )

    # 막을때 뭐라고 할 지
    response_message: str | None = Field(
        default=None,
        example="에어컨과 냉장고는 동시에 수리할 수 없습니다. 하나씩 따로 예약해 주세요.",
    )

    priority: int = Field(default=0, example=100)
    is_active: bool = Field(default=True)


class RuleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None

    rule_type: str | None = None
    trigger_condition: str | None = None
    instruction: str | None = None
    filters: list[str] | None = None

    action_type: str | None = None
    response_message: str | None = None

    priority: int | None = None
    is_active: bool | None = None


class RuleTestRequest(BaseModel):
    organization_id: str = Field(..., example="org_test")
    intent: str | None = Field(default=None, example="reservation")
    message: str = Field(
        ...,
        example="에어컨이랑 냉장고 같이 수리 예약하고 싶어요",
    )


@router.post("")
def create_rule_api(req: RuleCreateRequest):
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
    rules = list_rules(organization_id)

    return {
        "organization_id": organization_id,
        "count": len(rules),
        "items": rules,
    }


@router.get("/{rule_id}")
def get_rule_api(rule_id: str, organization_id: str):
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
def delete_rule_api(rule_id: str, organization_id: str):
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


@router.post("/test")
def test_rule_api(req: RuleTestRequest):
    active_rules = get_active_rules(req.organization_id)

    matched_rules = filter_rules_by_intent(
        rules=active_rules,
        intent=req.intent,
        user_message=req.message,
    )

    block_rules = get_block_rules(matched_rules)
    warn_rules = get_warn_rules(matched_rules)
    handoff_rules = get_handoff_rules(matched_rules)

    if block_rules:
        action_result = "block"
        response_message = (
            block_rules[0].get("response_message")
            or block_rules[0].get("instruction")
        )

    elif handoff_rules:
        action_result = "handoff"
        response_message = (
            handoff_rules[0].get("response_message")
            or handoff_rules[0].get("instruction")
        )

    elif warn_rules:
        action_result = "warn"
        response_message = (
            warn_rules[0].get("response_message")
            or warn_rules[0].get("instruction")
        )

    else:
        action_result = "allow"
        response_message = None

    return {
        "organization_id": req.organization_id,
        "message": req.message,
        "intent": req.intent,
        "matched_count": len(matched_rules),
        "action_result": action_result,
        "response_message": response_message,
        "matched_rules": matched_rules,
    }