from unittest.mock import MagicMock, patch

import pytest

from app.tasks.flow_generator import (
    ensure_template_ui_connections,
    generate_task_flow_from_template,
    load_task_flow_template,
)


def test_load_reservation_create_template():
    template = load_task_flow_template("reservation_create")
    assert template["template_key"] == "reservation_create"
    assert template["flow"]["trigger_intent"] == "reservation_create"
    assert len(template["nodes"]) >= 10
    assert len(template["edges"]) >= 10


def test_ensure_template_ui_connections_from_edges_only():
    template = {
        "nodes": [
            {
                "node_key": "start",
                "node_type": "instruction",
                "config": {"next_step_mode": "single"},
            },
            {
                "node_key": "lookup",
                "node_type": "function",
                "config": {},
            },
            {
                "node_key": "found_end",
                "node_type": "message",
                "config": {"next_step_mode": "end"},
            },
            {
                "node_key": "not_found_end",
                "node_type": "message",
                "config": {"next_step_mode": "end"},
            },
        ],
        "edges": [
            {
                "source_node_key": "start",
                "target_node_key": "lookup",
                "edge_type": "single",
                "condition_type": "always",
                "condition_config": {},
                "is_failure_edge": False,
                "priority": 100,
            },
            {
                "source_node_key": "lookup",
                "target_node_key": "found_end",
                "edge_type": "condition",
                "condition_type": "equals",
                "condition_config": {
                    "value": True,
                    "variable": "memory.has_reservations",
                },
                "is_failure_edge": False,
                "priority": 100,
            },
            {
                "source_node_key": "lookup",
                "target_node_key": "not_found_end",
                "edge_type": "condition",
                "condition_type": "equals",
                "condition_config": {
                    "value": False,
                    "variable": "memory.has_reservations",
                },
                "is_failure_edge": False,
                "priority": 200,
            },
        ],
    }

    patched = ensure_template_ui_connections(template)
    by_key = {node["node_key"]: node for node in patched["nodes"]}

    assert by_key["start"]["config"]["next_node_key"] == "lookup"
    assert by_key["lookup"]["config"]["branch_node_key"] == "found_end"
    assert by_key["lookup"]["config"]["fallback_node_key"] == "not_found_end"
    assert "memory.has_reservations == true" in by_key["lookup"]["config"]["branch_condition"]


def test_generate_skips_when_trigger_intent_exists():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"id": "existing-flow", "name": "기존 플로우", "trigger_intent": "reservation_create"}]
    )

    result = generate_task_flow_from_template(
        client,
        organization_id="org-1",
        template_key="reservation_create",
        overwrite=False,
    )

    assert result.skipped is True
    assert result.skip_reason == "trigger_intent_already_exists"
    assert result.flow_id == "existing-flow"
    client.table.return_value.insert.assert_not_called()


@patch("app.tasks.flow_generator.invalidate_enabled_flow_cache")
def test_generate_creates_flow_nodes_edges(mock_invalidate):
    client = MagicMock()

    def table(name):
        mock = MagicMock()
        if name == "task_flows":
            mock.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
                data=[]
            )
            mock.insert.return_value.execute.return_value = MagicMock(
                data=[{"id": "new-flow-id"}]
            )
        elif name in {"task_nodes", "task_edges"}:
            mock.insert.return_value.execute.return_value = MagicMock(data=[{}])
        return mock

    client.table.side_effect = table

    result = generate_task_flow_from_template(
        client,
        organization_id="org-1",
        template_key="reservation_lookup",
        overwrite=False,
    )

    assert result.created is True
    assert result.flow_id == "new-flow-id"
    assert result.node_count == 4
    assert result.edge_count == 3
    mock_invalidate.assert_called_once_with("org-1")
