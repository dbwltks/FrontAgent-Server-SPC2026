from datetime import date, datetime, time
from unittest.mock import patch

from app.tasks.function_registry import _build_start_end


@patch("app.tasks.function_registry.repo_get_service")
@patch("app.tasks.function_registry.repo_get_service_item")
def test_build_start_end_falls_back_to_default_duration_for_service_item(
    repo_get_service_item,
    repo_get_service,
):
    repo_get_service_item.return_value = {
        "id": "service-item-id",
        "service_id": "service-id",
        "duration_minutes": None,
    }
    repo_get_service.return_value = {
        "id": "service-id",
        "duration_minutes": None,
    }

    start_at, end_at = _build_start_end(
        params={
            "organization_id": "org-id",
            "service_item_id": "service-item-id",
            "date": date(2026, 7, 3),
            "time": time(9, 30),
        },
        variables={},
    )

    assert start_at == datetime.fromisoformat("2026-07-03T09:30:00+09:00")
    assert end_at == datetime.fromisoformat("2026-07-03T10:30:00+09:00")
    repo_get_service.assert_called_once_with(
        organization_id="org-id",
        service_id="service-id",
    )
