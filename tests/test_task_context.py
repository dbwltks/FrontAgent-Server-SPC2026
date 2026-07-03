import unittest

from app.graph.task_context import slim_task_result


class TaskContextTests(unittest.TestCase):
    def test_slim_task_result_drops_variables(self):
        slim = slim_task_result(
            {
                "status": "waiting_user_input",
                "current_node_key": "ask_name",
                "message": "성함이 어떻게 되시나요?",
                "variables": {
                    "service_item_name": "화장실 청소",
                    "customer_name": "유지산",
                },
                "trace": [{"node_key": "ask_name"}],
            }
        )

        self.assertEqual(slim["status"], "waiting_user_input")
        self.assertEqual(slim["message"], "성함이 어떻게 되시나요?")
        self.assertNotIn("variables", slim)


if __name__ == "__main__":
    unittest.main()
