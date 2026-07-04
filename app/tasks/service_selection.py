from typing import Any
import re

from app.tasks.function_registry import reservation_resolve_service_item
from app.tasks.memory import TaskMemory
from app.tasks.types import ExecutorResult

_PHONE_PATTERN = re.compile(r"^01[0-9]")
_SELECTION_NUMBER_PATTERN = re.compile(r"(\d+)")


def build_service_selection_message(
    *,
    variables: dict[str, Any],
    current_node_key: str | None = None,
    status: str | None = None,
) -> str | None:
    if status is not None and status != "waiting_user_input":
        return None
    if current_node_key is not None and current_node_key != "ask_service":
        return None

    available_services = variables.get("available_services") or {}
    services = available_services.get("services") or []
    if not services:
        return None

    service_names = [
        str(service.get("name")).strip()
        for service in services
        if isinstance(service, dict) and service.get("name")
    ]
    if not service_names:
        return None

    return f"어떤 서비스를 원하시나요? {', '.join(service_names)} 중에서 선택해 주세요."


def _looks_like_phone_number(text: str) -> bool:
    digits = re.sub(r"\D", "", (text or "").strip())
    return len(digits) >= 10 and digits.startswith("01")


def _parse_reservation_selection_number(text: str | None) -> int | None:
    if not text or _looks_like_phone_number(text):
        return None

    match = _SELECTION_NUMBER_PATTERN.search(text.strip())
    if not match:
        return None

    number = int(match.group(1))
    return number if number > 0 else None


def build_lookup_result_message(variables: dict[str, Any]) -> str | None:
    options = variables.get("reservation_options") or []
    labels = [
        str(option.get("label")).strip()
        for option in options
        if isinstance(option, dict) and option.get("label")
    ]
    if not labels:
        return None

    listing = "\n".join(labels)
    return f"입력하신 전화번호로 조회된 예약입니다.\n{listing}"


def build_cancel_selection_message(variables: dict[str, Any]) -> str | None:
    options = variables.get("cancelable_options") or []
    labels = [
        str(option.get("label")).strip()
        for option in options
        if isinstance(option, dict) and option.get("label")
    ]
    if not labels:
        return None

    listing = "\n".join(labels)
    return (
        "취소 가능한 예약을 찾았습니다. 취소할 예약 번호를 알려주세요.\n"
        f"{listing}"
    )


def try_fast_path_ask_cancel_number_instruction(
    *,
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None,
) -> ExecutorResult | None:
    """
    ask_cancel_number instruction 노드에서 LLM 없이 예약 목록 안내·번호 선택 처리.
    """
    if node.get("node_key") != "ask_cancel_number":
        return None

    variables = memory.to_dict()
    options = variables.get("cancelable_options") or []
    if not options:
        return None

    if user_message:
        selection = _parse_reservation_selection_number(user_message)
        if selection is not None and 1 <= selection <= len(options):
            return ExecutorResult(
                status="success",
                message=None,
                memory_updates={"selected_reservation_number": str(selection)},
                next_behavior="evaluate_edges",
            )

    selection_message = build_cancel_selection_message(variables)
    if not selection_message:
        return None

    return ExecutorResult(
        status="success",
        message=selection_message,
        memory_updates={},
        next_behavior="wait_user",
    )


def _memory_updates_from_resolve_result(result: dict[str, Any], user_message: str) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "service_item_text": user_message,
        "resolve_service_item_result": result,
    }
    if result.get("service_item_id"):
        updates["service_item_id"] = result["service_item_id"]
    if result.get("service_item_name"):
        updates["service_item_name"] = result["service_item_name"]
    service_item = result.get("service_item")
    if isinstance(service_item, dict) and service_item.get("service_id"):
        updates["service_id"] = service_item["service_id"]
    return updates


def try_fast_path_ask_service_instruction(
    *,
    node: dict[str, Any],
    memory: TaskMemory,
    user_message: str | None,
    organization_id: str | None,
) -> ExecutorResult | None:
    """
    ask_service instruction 노드의 첫 진입 시 LLM 없이 서비스 매칭/선택 질문을 처리.
    """
    if node.get("node_key") != "ask_service":
        return None

    variables = memory.to_dict()

    # context bridge로 이미 서비스가 확정된 경우 ask_service를 건너뛴다.
    if variables.get("service_item_id"):
        return ExecutorResult(
            status="success",
            message=None,
            memory_updates={},
            next_behavior="evaluate_edges",
        )

    if not user_message or not organization_id:
        return None

    if not (variables.get("available_services") or {}).get("services"):
        return None

    resolve_result = reservation_resolve_service_item(
        params={
            "organization_id": organization_id,
            "service_item_text": user_message.strip(),
        },
        variables=variables,
    )

    if resolve_result.get("resolved"):
        return ExecutorResult(
            status="success",
            message=None,
            memory_updates=_memory_updates_from_resolve_result(
                resolve_result,
                user_message.strip(),
            ),
            next_behavior="evaluate_edges",
        )

    selection_message = build_service_selection_message(
        variables=variables,
        current_node_key="ask_service",
        status="waiting_user_input",
    )

    if not selection_message:
        return None

    return ExecutorResult(
        status="success",
        message=selection_message,
        memory_updates={"service_item_text": user_message.strip()},
        next_behavior="wait_user",
    )
