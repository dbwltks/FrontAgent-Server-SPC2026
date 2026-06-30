import time
from typing import Any

from supabase import Client

from app.core.db import supabase as default_supabase_client


ACTIVE_TASK_STATUSES = [
    "running",
    "waiting_user_input",
    "approval_waiting",
]

# task_flows는 관리자가 가끔 수정하는 정적 메타데이터다(organization 설정과 동급).
# task_node 실행 경로(매 대화 턴)에서 매번 DB를 왕복하지 않도록 짧은 TTL로
# 캐싱하고, app/api/task_flows.py에서 플로우 메타데이터를 수정/삭제/생성하면
# 해당 organization 전체를 무효화한다.
_ENABLED_FLOW_CACHE_TTL_SECONDS = 60
_enabled_flow_cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}


def invalidate_enabled_flow_cache(organization_id: str) -> None:
    for key in [key for key in _enabled_flow_cache if key[0] == organization_id]:
        _enabled_flow_cache.pop(key, None)


class TaskRepository:
    def __init__(self, client: Client | None = None):
        # 매 대화 턴(conversation_node)마다 TaskRepository()가 새로 생성되므로,
        # 여기서 매번 create_client()를 부르면 그때마다 새 httpx 클라이언트가
        # 만들어져 불필요한 연결 비용이 응답 지연에 누적된다. app/core/db.py가
        # 이미 모듈 전역으로 들고 있는 클라이언트를 기본값으로 재사용한다.
        self.client = client or default_supabase_client

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
        cache_key = (organization_id, task_type)
        cached = _enabled_flow_cache.get(cache_key)
        now = time.monotonic()

        if cached is not None and now - cached[0] < _ENABLED_FLOW_CACHE_TTL_SECONDS:
            return cached[1]

        flow = self._find_enabled_flow_for_task_type_uncached(organization_id, task_type)
        _enabled_flow_cache[cache_key] = (now, flow)
        return flow

    def _find_enabled_flow_for_task_type_uncached(
        self,
        organization_id: str,
        task_type: str,
    ) -> dict[str, Any] | None:
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