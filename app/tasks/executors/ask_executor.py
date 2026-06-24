from typing import Any

from app.tasks.memory import TaskMemory
from app.tasks.template_renderer import render_text_template
from app.tasks.types import ExecutorResult
from app.tasks.task_slot_extractor import extract_slot_value

def execute_ask_node(
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None = None,
    is_waiting_input: bool = False,
) -> ExecutorResult:
    config = node.get("config") or {}
    variable_name = config.get("variable_name")

    question = (
        config.get("question")
        or node.get("label")
        or "필요한 정보를 입력해 주세요."
    )

    if is_waiting_input:
        if not variable_name:
            return ExecutorResult(
                status="failed",
                message="Ask Node에 variable_name이 설정되어 있지 않습니다.",
                next_behavior="fail",
                error={
                    "code": "ASK_NODE_VARIABLE_NAME_MISSING",
                    "message": "config.variable_name is required.",
                },
            )
        
        extracted_value = extract_slot_value(
            node=node,
            memory=memory,
            user_message=user_message,
        )

        return ExecutorResult(
            status="success",
            message=None,
            memory_updates={
                variable_name: extracted_value,
            },
            next_behavior="evaluate_edges",
        )

    fallback_message = render_text_template(question, memory)
    print("ASK_EXECUTOR_AI_GENERATION_START:", node.get("node_key"))
    try:
        from app.tasks.task_response_generator import generate_task_question

        message = generate_task_question(
            node=node,
            memory=memory,
            fallback_message=fallback_message,
        )
    except Exception as e:
        print("ASK_NODE_AI_MESSAGE_FAILED:", repr(e))
        message = fallback_message

    return ExecutorResult(
        status="success",
        message=message,
        next_behavior="wait_user",
    )