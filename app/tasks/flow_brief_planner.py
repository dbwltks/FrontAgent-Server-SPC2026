"""
사용자 지시(brief)를 현재 태스크 구조(템플릿 + 노드/함수 레지스트리) 안에서
해석해 플로우 생성 계획을 만든다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.tasks.flow_generator import AVAILABLE_TEMPLATES

TemplateKey = Literal["reservation_create", "reservation_lookup", "reservation_cancel"]

ALLOWED_CHANNELS = {"chat", "voice"}

SLOTS_BY_TEMPLATE: dict[str, set[str]] = {
    "reservation_create": {
        "service_item",
        "party_size",
        "reservation_date",
        "reservation_time",
        "customer_phone",
        "customer_name",
    },
    "reservation_lookup": {"customer_phone"},
    "reservation_cancel": {"customer_phone", "reservation_id"},
}


class TaskFlowBriefPlan(BaseModel):
    template_key: TemplateKey = Field(
        description="사용자 지시에 가장 맞는 표준 템플릿. 새 함수/노드 타입을 만들지 않는다.",
    )
    name: str = Field(description="조직에 보여줄 태스크 플로우 이름")
    description: str = Field(description="이 태스크가 하는 일을 1~2문장으로")
    trigger_description: str = Field(
        description="언제 이 태스크를 시작할지. task_flows.trigger_description에 저장",
    )
    trigger_examples: list[str] = Field(
        min_length=3,
        max_length=8,
        description="고객이 실제로 말할 법한 시작 문장 예시",
    )
    allowed_channels: list[str] = Field(
        default_factory=lambda: ["chat", "voice"],
        description="chat, voice 중 허용 채널",
    )
    required_slots: list[str] = Field(
        default_factory=list,
        description="반드시 수집할 memory 슬롯. 템플릿별 허용 목록 안에서만 선택",
    )
    assistant_tone_hint: str | None = Field(
        default=None,
        description="질문/안내 톤에 대한 짧은 힌트. 예: '존댓말, 한 번에 하나씩'",
    )
    reasoning: str = Field(description="왜 이 template/slot을 골랐는지 짧게")

    @field_validator("allowed_channels")
    @classmethod
    def validate_channels(cls, channels: list[str]) -> list[str]:
        normalized = [c.strip() for c in channels if c and c.strip()]
        if not normalized:
            return ["chat", "voice"]
        invalid = [c for c in normalized if c not in ALLOWED_CHANNELS]
        if invalid:
            raise ValueError(f"Unsupported channels: {invalid}")
        return normalized

    @field_validator("required_slots")
    @classmethod
    def normalize_slots(cls, slots: list[str]) -> list[str]:
        return [s.strip() for s in slots if s and s.strip()]


TASK_FLOW_BRIEF_INSTRUCTIONS = f"""
너는 Front Agent의 Task Flow 생성기다.

사용자가 자연어로 "어떤 태스크를 만들고 싶은지" 설명하면,
이미 검증된 표준 템플릿 중 하나를 고르고 trigger/slot 설정을 맞춘 계획만 만든다.

[중요 제약]
- template_key는 반드시 다음 중 하나만: {", ".join(AVAILABLE_TEMPLATES)}
- 새 node_type, function_name, edge 구조를 발명하지 마라.
- 예약 생성/접수/신청 → reservation_create
- 예약 조회/내 예약 확인 → reservation_lookup
- 예약 취소 → reservation_cancel
- allowed_channels는 chat, voice만 사용
- required_slots는 template별 허용 목록 안에서만:
  - reservation_create: {", ".join(sorted(SLOTS_BY_TEMPLATE["reservation_create"]))}
  - reservation_lookup: {", ".join(sorted(SLOTS_BY_TEMPLATE["reservation_lookup"]))}
  - reservation_cancel: {", ".join(sorted(SLOTS_BY_TEMPLATE["reservation_cancel"]))}
- trigger_examples는 고객이 실제로 말할 법한 한국어 문장 3~8개
- 정책/가격 FAQ만 묻는 경우는 태스크가 아니라 지식 FAQ이므로 reservation_create를 고르지 마라

[출력]
구조화된 JSON 계획만 반환한다.
""".strip()


def validate_brief_plan(plan: TaskFlowBriefPlan) -> TaskFlowBriefPlan:
    allowed_slots = SLOTS_BY_TEMPLATE.get(plan.template_key, set())
    invalid_slots = [slot for slot in plan.required_slots if slot not in allowed_slots]
    if invalid_slots:
        raise ValueError(
            f"required_slots {invalid_slots} are not allowed for template {plan.template_key}. "
            f"Allowed: {sorted(allowed_slots)}"
        )
    if plan.template_key not in AVAILABLE_TEMPLATES:
        raise ValueError(f"Invalid template_key: {plan.template_key}")
    return plan
