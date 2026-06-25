from app.tasks.executors.condition_executor import execute_condition_node
from app.tasks.executors.function_executor import execute_function_node
from app.tasks.executors.instruction_executor import execute_instruction_node
from app.tasks.executors.message_executor import execute_message_node


EXECUTOR_MAP = {
    "message": execute_message_node,
    "instruction": execute_instruction_node,
    "condition": execute_condition_node,
    "function": execute_function_node,
}