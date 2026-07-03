from unittest.mock import MagicMock, patch

import pytest

from app.tasks.flow_generator import (
    generate_task_flow_from_template,
    load_task_flow_template,
)


def test_load_reservation_create_template():
    template = load_task_flow_template("reservation_create")
    assert template["template_key"] == "reservation_create"
    assert template["flow"]["trigger_intent"] == "reservation_create"
    assert len(template["nodes"]) >= 10
    assert len(template["edges"]) >= 10


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
