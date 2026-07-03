from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from supabase import Client

from app.tasks.repository import invalidate_enabled_flow_cache

TemplateKey = Literal[
    "reservation_create",
    "reservation_lookup",
    "reservation_cancel",
    "all",
]

AVAILABLE_TEMPLATES: tuple[str, ...] = (
    "reservation_create",
    "reservation_lookup",
    "reservation_cancel",
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class GeneratedTaskFlow:
    template_key: str
    flow_id: str
    name: str
    trigger_intent: str | None
    node_count: int
    edge_count: int
    created: bool
    skipped: bool = False
    skip_reason: str | None = None


def load_task_flow_template(template_key: str) -> dict[str, Any]:
    if template_key not in AVAILABLE_TEMPLATES:
        raise ValueError(f"Unknown template: {template_key}")

    path = _TEMPLATES_DIR / f"{template_key}.json"
    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def _find_existing_flow(
    client: Client,
    *,
    organization_id: str,
    trigger_intent: str | None,
) -> dict[str, Any] | None:
    if not trigger_intent:
        return None

    response = (
        client.table("task_flows")
        .select("id, name, trigger_intent")
        .eq("organization_id", organization_id)
        .eq("trigger_intent", trigger_intent)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def _delete_flow(client: Client, flow_id: str) -> None:
    client.table("task_edges").delete().eq("flow_id", flow_id).execute()
    client.table("task_nodes").delete().eq("flow_id", flow_id).execute()
    client.table("task_flows").delete().eq("id", flow_id).execute()


def generate_task_flow_from_template(
    client: Client,
    *,
    organization_id: str,
    template_key: str,
    overwrite: bool = False,
    is_enabled: bool = True,
    trigger_description: str | None = None,
    trigger_examples: list[str] | None = None,
    template: dict[str, Any] | None = None,
) -> GeneratedTaskFlow:
    loaded_template = template or load_task_flow_template(template_key)
    if loaded_template.get("template_key") and loaded_template["template_key"] != template_key:
        raise ValueError("template_key mismatch between argument and template payload")

    flow_meta = dict(loaded_template["flow"])
    trigger_intent = flow_meta.get("trigger_intent")

    existing = _find_existing_flow(
        client,
        organization_id=organization_id,
        trigger_intent=trigger_intent,
    )
    if existing and not overwrite:
        return GeneratedTaskFlow(
            template_key=template_key,
            flow_id=existing["id"],
            name=existing.get("name") or flow_meta.get("name", ""),
            trigger_intent=trigger_intent,
            node_count=0,
            edge_count=0,
            created=False,
            skipped=True,
            skip_reason="trigger_intent_already_exists",
        )

    if existing and overwrite:
        _delete_flow(client, existing["id"])

    if trigger_description is not None:
        flow_meta["trigger_description"] = trigger_description
    if trigger_examples is not None:
        flow_meta["trigger_examples"] = trigger_examples

    flow_meta["organization_id"] = organization_id
    flow_meta["is_enabled"] = is_enabled

    flow_response = client.table("task_flows").insert(flow_meta).execute()
    flow_rows = flow_response.data or []
    if not flow_rows:
        raise RuntimeError(f"Failed to create task flow for template: {template_key}")

    flow_id = flow_rows[0]["id"]

    node_payloads = [
        {
            **node,
            "flow_id": flow_id,
        }
        for node in loaded_template.get("nodes", [])
    ]
    if node_payloads:
        client.table("task_nodes").insert(node_payloads).execute()

    edge_payloads = [
        {
            **edge,
            "flow_id": flow_id,
        }
        for edge in loaded_template.get("edges", [])
    ]
    if edge_payloads:
        client.table("task_edges").insert(edge_payloads).execute()

    invalidate_enabled_flow_cache(organization_id)

    return GeneratedTaskFlow(
        template_key=template_key,
        flow_id=flow_id,
        name=flow_meta.get("name") or "",
        trigger_intent=trigger_intent,
        node_count=len(node_payloads),
        edge_count=len(edge_payloads),
        created=True,
    )


def generate_task_flows_from_templates(
    client: Client,
    *,
    organization_id: str,
    template: TemplateKey,
    overwrite: bool = False,
    is_enabled: bool = True,
) -> list[GeneratedTaskFlow]:
    keys = list(AVAILABLE_TEMPLATES) if template == "all" else [template]
    return [
        generate_task_flow_from_template(
            client,
            organization_id=organization_id,
            template_key=template_key,
            overwrite=overwrite,
            is_enabled=is_enabled,
        )
        for template_key in keys
    ]


async def generate_task_flow_from_brief(
    client: Client,
    *,
    organization_id: str,
    brief: str,
    overwrite: bool = False,
    is_enabled: bool = True,
) -> tuple[TaskFlowBriefPlan, GeneratedTaskFlow]:
    from app.providers.langchain_provider import generate_structured
    from app.tasks.flow_brief_applier import apply_brief_plan_to_template
    from app.tasks.flow_brief_planner import (
        TASK_FLOW_BRIEF_INSTRUCTIONS,
        TaskFlowBriefPlan,
        validate_brief_plan,
    )

    raw_plan = await generate_structured(
        organization_id,
        TASK_FLOW_BRIEF_INSTRUCTIONS,
        brief.strip(),
        TaskFlowBriefPlan,
    )
    plan = validate_brief_plan(raw_plan)

    base_template = load_task_flow_template(plan.template_key)
    patched_template = apply_brief_plan_to_template(base_template, plan)

    result = generate_task_flow_from_template(
        client,
        organization_id=organization_id,
        template_key=plan.template_key,
        overwrite=overwrite,
        is_enabled=is_enabled,
        template=patched_template,
    )
    return plan, result
