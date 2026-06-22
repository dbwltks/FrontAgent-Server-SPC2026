from typing import Any


def get_value_by_path(data: dict[str, Any], path: str | None) -> Any:
    """
    예:
    path = "memory.is_available"
    data = {"is_available": true}
    """

    if not path:
        return None

    if path.startswith("memory."):
        path = path.replace("memory.", "", 1)

    current: Any = data

    for part in path.split("."):
        if not isinstance(current, dict):
            return None

        current = current.get(part)

    return current


def evaluate_edge_condition(
    edge: dict[str, Any],
    variables: dict[str, Any],
) -> bool:
    condition_type = edge.get("condition_type") or "always"
    condition_config = edge.get("condition_config") or {}

    if condition_type == "always":
        return True

    if condition_type == "request_failed":
        return False

    variable_path = condition_config.get("variable")
    expected_value = condition_config.get("value")
    actual_value = get_value_by_path(variables, variable_path)

    if condition_type == "equals":
        return actual_value == expected_value

    if condition_type == "not_equals":
        return actual_value != expected_value

    if condition_type == "exists":
        return actual_value is not None

    if condition_type == "not_exists":
        return actual_value is None

    if condition_type == "contains":
        if actual_value is None:
            return False
        return expected_value in actual_value

    if condition_type == "greater_than":
        if actual_value is None or expected_value is None:
            return False
        return actual_value > expected_value

    if condition_type == "less_than":
        if actual_value is None or expected_value is None:
            return False
        return actual_value < expected_value

    if condition_type == "in":
        if not isinstance(expected_value, list):
            return False
        return actual_value in expected_value

    if condition_type == "not_in":
        if not isinstance(expected_value, list):
            return False
        return actual_value not in expected_value

    return False


def select_next_edge(
    edges: list[dict[str, Any]],
    variables: dict[str, Any],
) -> dict[str, Any] | None:
    """
    일반 성공 흐름에서 다음 Edge를 선택한다.
    failure edge는 제외한다.
    """

    sorted_edges = sorted(edges, key=lambda edge: edge.get("priority", 100))

    for edge in sorted_edges:
        if edge.get("is_failure_edge") is True:
            continue

        if evaluate_edge_condition(edge, variables):
            return edge

    return None


def select_failure_edge(
    edges: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Function Node / Instruction Node / Code Node 등이 실패했을 때
    실패 분기 Edge를 선택한다.
    """

    sorted_edges = sorted(edges, key=lambda edge: edge.get("priority", 100))

    for edge in sorted_edges:
        if edge.get("is_failure_edge") is True:
            return edge

        if edge.get("condition_type") == "request_failed":
            return edge

    return None