from unittest.mock import MagicMock, patch

from app.tasks.repository import TaskRepository


class _TableMock:
    def __init__(self, select_data=None):
        self.select_data = select_data or []
        self.insert = MagicMock(return_value=self)
        self.update = MagicMock(return_value=self)
        self.select = MagicMock(return_value=self)
        self.eq = MagicMock(return_value=self)
        self.order = MagicMock(return_value=self)
        self.limit = MagicMock(return_value=self)

    def execute(self):
        return MagicMock(data=self.select_data)


class _ClientMock:
    def __init__(self, table):
        self._table = table

    def table(self, _name):
        return self._table


@patch("app.tasks.repository.redis_client")
def test_create_session_reuses_existing_non_active_session(redis_client):
    existing_session = {
        "id": "task-session-id",
        "organization_id": "org-id",
        "session_id": "session-id",
        "flow_id": "flow-id",
        "current_node_key": "complete",
        "waiting_node_key": None,
        "variables": {"old": True},
        "status": "completed",
        "last_error": {"code": "OLD"},
    }
    table = _TableMock(select_data=[existing_session])

    session = TaskRepository(client=_ClientMock(table)).create_session(
        organization_id="org-id",
        session_id="session-id",
        flow_id="flow-id",
        current_node_key="start",
        variables={},
    )

    assert session["id"] == "task-session-id"
    assert session["current_node_key"] == "start"
    assert session["status"] == "running"
    assert session["variables"] == {}
    assert session["last_error"] is None
    table.insert.assert_not_called()
    table.update.assert_called_once()
    redis_client.setex.assert_called_once()
