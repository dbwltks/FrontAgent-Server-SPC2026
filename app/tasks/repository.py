import json
import threading
import time
from typing import Any

from supabase import Client

from app.core.db import supabase as default_supabase_client
from app.core.redis import redis_client


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
_enabled_flows_list_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# Redis 캐시 TTL
_FLOW_META_TTL = 300        # 노드/엣지 메타데이터: 5분 (관리자 수정 후 최대 5분 지연)
_TASK_SESSION_TTL = 1800    # 태스크 세션: 30분 (통화 중 유지)


def _rkey_node(flow_id: str, node_key: str) -> str:
    return f"task:node:{flow_id}:{node_key}"

def _rkey_edges(flow_id: str, source_node_key: str) -> str:
    return f"task:edges:{flow_id}:{source_node_key}"

def _rkey_start_node(flow_id: str) -> str:
    return f"task:start_node:{flow_id}"

def _rkey_session(organization_id: str, session_id: str) -> str:
    return f"task:session:{organization_id}:{session_id}"


def invalidate_flow_meta_cache(flow_id: str) -> None:
    pattern = f"task:*:{flow_id}:*"
    keys = redis_client.keys(pattern)
    if keys:
        redis_client.delete(*keys)


def invalidate_enabled_flow_cache(organization_id: str) -> None:
    for key in [key for key in _enabled_flow_cache if key[0] == organization_id]:
        _enabled_flow_cache.pop(key, None)
    _enabled_flows_list_cache.pop(organization_id, None)


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

    def list_enabled_flows(self, organization_id: str) -> list[dict[str, Any]]:
        cached = _enabled_flows_list_cache.get(organization_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _ENABLED_FLOW_CACHE_TTL_SECONDS:
            return cached[1]

        response = (
            self.client.table("task_flows")
            .select(
                "id, name, trigger_intent, trigger_description, trigger_examples, "
                "allowed_channels, is_enabled, created_at"
            )
            .eq("organization_id", organization_id)
            .eq("is_enabled", True)
            .order("created_at", desc=True)
            .execute()
        )
        flows = response.data or []
        _enabled_flows_list_cache[organization_id] = (now, flows)
        return flows


    def find_active_session(
        self,
        organization_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        # Redis 우선 조회 — 세션 생성/업데이트 시 Redis에 쓰므로 DB 왕복 불필요.
        rkey = _rkey_session(organization_id, session_id)
        cached = redis_client.get(rkey)
        if cached:
            session = json.loads(cached)
            if session.get("status") in ACTIVE_TASK_STATUSES:
                return session
            return None

        # Redis miss → DB fallback (서버 재시작 등 예외 상황)
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
        if rows:
            redis_client.setex(rkey, _TASK_SESSION_TTL, json.dumps(rows[0]))
        return rows[0] if rows else None

    def get_flow(self, flow_id: str) -> dict[str, Any] | None:
        rkey = f"task:flow:{flow_id}"
        cached = redis_client.get(rkey)
        if cached:
            return json.loads(cached)

        response = (
            self.client.table("task_flows")
            .select("*")
            .eq("id", flow_id)
            .single()
            .execute()
        )
        data = response.data
        if data:
            redis_client.setex(rkey, _FLOW_META_TTL, json.dumps(data))
        return data

    def get_start_node(self, flow_id: str) -> dict[str, Any] | None:
        rkey = _rkey_start_node(flow_id)
        cached = redis_client.get(rkey)
        if cached:
            return json.loads(cached)

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
            redis_client.setex(rkey, _FLOW_META_TTL, json.dumps(start_rows[0]))
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
        if rows:
            redis_client.setex(rkey, _FLOW_META_TTL, json.dumps(rows[0]))
        return rows[0] if rows else None

    def get_node_by_key(
        self,
        flow_id: str,
        node_key: str,
    ) -> dict[str, Any] | None:
        rkey = _rkey_node(flow_id, node_key)
        cached = redis_client.get(rkey)
        if cached:
            return json.loads(cached)

        response = (
            self.client.table("task_nodes")
            .select("*")
            .eq("flow_id", flow_id)
            .eq("node_key", node_key)
            .single()
            .execute()
        )
        node = response.data
        if node:
            redis_client.setex(rkey, _FLOW_META_TTL, json.dumps(node))
        return node

    def list_edges_from(
        self,
        flow_id: str,
        source_node_key: str,
    ) -> list[dict[str, Any]]:
        rkey = _rkey_edges(flow_id, source_node_key)
        cached = redis_client.get(rkey)
        if cached:
            return json.loads(cached)

        response = (
            self.client.table("task_edges")
            .select("*")
            .eq("flow_id", flow_id)
            .eq("source_node_key", source_node_key)
            .order("priority")
            .execute()
        )
        edges = response.data or []
        redis_client.setex(rkey, _FLOW_META_TTL, json.dumps(edges))
        return edges

    def create_session(
        self,
        organization_id: str,
        session_id: str,
        flow_id: str,
        current_node_key: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import uuid
        session = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "session_id": session_id,
            "flow_id": flow_id,
            "current_node_key": current_node_key,
            "waiting_node_key": None,
            "variables": variables or {},
            "status": "running",
        }
        # Redis에 즉시 쓰고 DB는 백그라운드로 영속화한다.
        rkey = _rkey_session(organization_id, session_id)
        redis_client.setex(rkey, _TASK_SESSION_TTL, json.dumps(session))

        def _persist():
            try:
                self.client.table("task_sessions").insert(session).execute()
            except Exception:
                pass
        threading.Thread(target=_persist, daemon=True).start()

        return session

    def update_session(
        self,
        task_session_id: str,
        values: dict[str, Any],
        *,
        organization_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        # Redis 세션을 즉시 업데이트하고 DB는 백그라운드 영속화.
        if organization_id and session_id:
            rkey = _rkey_session(organization_id, session_id)
            raw = redis_client.get(rkey)
            if raw:
                session = json.loads(raw)
                session.update(values)
                redis_client.setex(rkey, _TASK_SESSION_TTL, json.dumps(session))

        def _persist():
            try:
                self.client.table("task_sessions").update(values).eq("id", task_session_id).execute()
            except Exception:
                pass
        threading.Thread(target=_persist, daemon=True).start()
        return None