from typing import Any

from supabase import Client, create_client

from app.core.config import settings


ACTIVE_TASK_STATUSES = [
    "running",
    "waiting_user_input",
    "approval_waiting",
]


class TaskRepository:
    def __init__(self, client: Client | None = None):
        self.client = client or create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )

    def find_enabled_flow_for_task_type(
        self,
        organization_id: str,
        task_type: str,
    ) -> dict[str, Any] | None:
        """
        decision_node가 판단한 task_type에 맞는 활성화된 task_flow를 찾는다.

        1순위: trigger_intent = task_type
        2순위: 기존 데모 플로우 이름 fallback
        """
        response = (
            self.client.table("task_flows")
            .select("*")
            .eq("organization_id", organization_id)
            .eq("is_enabled", True)
            .eq("trigger_intent", task_type)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        rows = response.data or []

        if rows:
            return rows[0]

        fallback_names = {
            "reservation_create": "예약 생성 플로우",
            "reservation_lookup": "예약 조회 플로우",
            "reservation_cancel": "예약 취소 플로우",
            "reservation_update": "예약 변경 플로우",
        }

        fallback_name = fallback_names.get(task_type)

        if not fallback_name:
            return None

        fallback_response = (
            self.client.table("task_flows")
            .select("*")
            .eq("organization_id", organization_id)
            .eq("is_enabled", True)
            .eq("name", fallback_name)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        fallback_rows = fallback_response.data or []

        return fallback_rows[0] if fallback_rows else None



    def find_active_session(
        self,
        organization_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        response = (
            self.client.table("task_sessions")
            .select("*")
            .eq("organization_id", organization_id)
            .eq("session_id", session_id)
            .in_("status", ACTIVE_TASK_STATUSES)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        rows = response.data or []
        return rows[0] if rows else None

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        response = (
            self.client.table("task_flows")
            .select("*")
            .eq("id", flow_id)
            .single()
            .execute()
        )

        return response.data

    def get_start_node(self, flow_id: str) -> dict[str, Any] | None:
        # MVP 기준:
        # 1순위: node_key = "start"
        # 2순위: 가장 먼저 생성된 노드
        start_response = (
            self.client.table("task_nodes")
            .select("*")
            .eq("flow_id", flow_id)
            .eq("node_key", "start")
            .limit(1)
            .execute()
        )

        start_rows = start_response.data or []
        if start_rows:
            return start_rows[0]

        response = (
            self.client.table("task_nodes")
            .select("*")
            .eq("flow_id", flow_id)
            .order("created_at")
            .limit(1)
            .execute()
        )

        rows = response.data or []
        return rows[0] if rows else None

    def get_node_by_key(
        self,
        flow_id: str,
        node_key: str,
    ) -> dict[str, Any] | None:
        response = (
            self.client.table("task_nodes")
            .select("*")
            .eq("flow_id", flow_id)
            .eq("node_key", node_key)
            .single()
            .execute()
        )

        return response.data

    def list_edges_from(
        self,
        flow_id: str,
        source_node_key: str,
    ) -> list[dict[str, Any]]:
        response = (
            self.client.table("task_edges")
            .select("*")
            .eq("flow_id", flow_id)
            .eq("source_node_key", source_node_key)
            .order("priority")
            .execute()
        )

        return response.data or []

    def create_session(
        self,
        organization_id: str,
        session_id: str,
        flow_id: str,
        current_node_key: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "organization_id": organization_id,
            "session_id": session_id,
            "flow_id": flow_id,
            "current_node_key": current_node_key,
            "waiting_node_key": None,
            "variables": variables or {},
            "status": "running",
        }

        response = (
            self.client.table("task_sessions")
            .insert(payload)
            .execute()
        )

        rows = response.data or []
        return rows[0]

    def update_session(
        self,
        task_session_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        response = (
            self.client.table("task_sessions")
            .update(values)
            .eq("id", task_session_id)
            .execute()
        )

        rows = response.data or []
        return rows[0] if rows else None