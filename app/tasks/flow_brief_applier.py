from __future__ import annotations

import copy
from typing import Any

from app.tasks.flow_brief_planner import TaskFlowBriefPlan

_INSTRUCTION_NODE_KEYS = {
    "reservation_create": ("ask_service", "ask_reservation_details"),
    "reservation_lookup": ("start",),
    "reservation_cancel": ("start", "ask_cancel_number"),
}


def _build_operator_instruction_block(plan: TaskFlowBriefPlan) -> str:
    slots = ", ".join(plan.required_slots) if plan.required_slots else "(템플릿 기본값)"
    lines = [
        "[운영자 맞춤 설정]",
        f"- 반드시 수집할 정보: {slots}",
    ]
    if plan.assistant_tone_hint:
        lines.append(f"- 안내 톤: {plan.assistant_tone_hint}")
    if plan.template_key == "reservation_create" and "party_size" not in plan.required_slots:
        lines.append("- party_size(인원수)는 이 업무에 필요 없으므로 가능하면 건너뛴다.")
    lines.append(f"- 태스크 목적: {plan.description}")
    return "\n".join(lines)


def apply_brief_plan_to_template(
    template: dict[str, Any],
    plan: TaskFlowBriefPlan,
) -> dict[str, Any]:
    """
    검증된 템플릿 JSON에 사용자 brief 계획을 반영한다.
    그래프 topology(노드/엣지/function_name)는 유지하고,
    trigger 메타와 instruction 노드 지시문만 맞춘다.
    """
    patched = copy.deepcopy(template)
    flow = patched.setdefault("flow", {})
    flow["name"] = plan.name
    flow["description"] = plan.description
    flow["trigger_description"] = plan.trigger_description
    flow["trigger_examples"] = plan.trigger_examples
    flow["allowed_channels"] = plan.allowed_channels

    operator_block = _build_operator_instruction_block(plan)
    node_keys = _INSTRUCTION_NODE_KEYS.get(plan.template_key, ())

    for node in patched.get("nodes", []):
        if node.get("node_key") not in node_keys:
            continue
        if node.get("node_type") != "instruction":
            continue
        config = dict(node.get("config") or {})
        instruction = (config.get("instruction") or "").strip()
        config["instruction"] = f"{operator_block}\n\n{instruction}".strip()
        node["config"] = config

    return patched
