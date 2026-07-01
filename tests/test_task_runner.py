import unittest

from app.tasks.runner import DynamicTaskRunner
from app.tasks.types import ExecutorResult


class _InputCaptureRepository:
    def __init__(self):
        self.updated_sessions = []

    def find_active_session(self, organization_id, session_id):
        return {
            "id": "task-session-id",
            "organization_id": organization_id,
            "session_id": session_id,
            "flow_id": "flow-id",
            "current_node_key": "ask_name",
            "waiting_node_key": "ask_name",
            "variables": {},
            "status": "waiting_user_input",
        }

    def get_node_by_key(self, flow_id, node_key):
        nodes = {
            "ask_name": {
                "node_key": "ask_name",
                "label": "name",
                "node_type": "instruction",
                "config": {"next_step_mode": "single", "next_node_key": "ask_new_time"},
            },
            "ask_new_time": {
                "node_key": "ask_new_time",
                "label": "new time",
                "node_type": "instruction",
                "config": {"next_step_mode": "single", "next_node_key": "done"},
            },
            "done": {
                "node_key": "done",
                "label": "done",
                "node_type": "message",
                "config": {"next_step_mode": "end", "message": "done"},
            },
        }
        return nodes.get(node_key)

    def list_edges_from(self, flow_id, source_node_key):
        return []

    def update_session(self, task_session_id, values, *, organization_id=None, session_id=None):
        self.updated_sessions.append(values)


class DynamicTaskRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_user_input_is_consumed_only_by_waiting_node(self):
        seen_inputs = {}
        runner = DynamicTaskRunner(repository=_InputCaptureRepository())

        async def fake_execute_node(node, memory, user_message, is_waiting_input, organization_id):
            seen_inputs[node["node_key"]] = user_message
            if node["node_key"] == "ask_new_time":
                return ExecutorResult(
                    status="success",
                    message="다른 시간이나 날짜를 알려주세요.",
                    next_behavior="wait_user",
                )
            return ExecutorResult(
                status="success",
                memory_updates={"customer_name": "김길동"},
                next_behavior="evaluate_edges",
            )

        runner._execute_node = fake_execute_node

        result = await runner.run(
            organization_id="org-id",
            session_id="session-id",
            user_message="김길동이요",
        )

        self.assertEqual(seen_inputs["ask_name"], "김길동이요")
        self.assertIsNone(seen_inputs["ask_new_time"])
        self.assertEqual(result.status, "waiting_user_input")
        self.assertEqual(result.current_node_key, "ask_new_time")
