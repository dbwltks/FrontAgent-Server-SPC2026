from app.tasks.executors.ask_executor import execute_ask_node
from app.tasks.executors.end_executor import execute_end_node
from app.tasks.executors.instruction_executor import execute_instruction_node
from app.tasks.executors.message_executor import execute_message_node


EXECUTOR_MAP = {
    "message": execute_message_node,
    "ask": execute_ask_node,
    "instruction": execute_instruction_node,
    "end": execute_end_node,
}