from typing import Any

from app.tasks.edge_evaluator import select_failure_edge, select_next_edge
from app.tasks.executors import EXECUTOR_MAP
from app.tasks.memory import TaskMemory
from app.tasks.repository import TaskRepository
from app.tasks.types import ExecutorResult, TaskRunResponse


class DynamicTaskRunner:
    def __init__(self, repository: TaskRepository | None = None):
        self.repository = repository or TaskRepository()

    def run(
        self,
        organization_id: str,
        session_id: str,
        user_message: str,
        flow_id: str | None = None,
    ) -> TaskRunResponse:
        """
        MVP 2단계 기준 실행 방식.

        1. 진행 중 task_session이 있으면 이어서 실행
        2. 없으면 flow_id가 전달된 경우 해당 flow를 수동 시작
        3. flow_id가 없고 진행 중 session도 없으면 handled=False 반환

        trigger 판단은 아직 연결하지 않는다.
        """

        task_session = self.repository.find_active_session(
            organization_id=organization_id,
            session_id=session_id,
        )

        if task_session is None:
            if flow_id is None:
                return TaskRunResponse(
                    handled=False,
                    message=None,
                    status=None,
                    variables={},
                )

            task_session = self._start_session(
                organization_id=organization_id,
                session_id=session_id,
                flow_id=flow_id,
            )

        return self._run_session(
            task_session=task_session,
            user_message=user_message,
        )

    def _start_session(
        self,
        organization_id: str,
        session_id: str,
        flow_id: str,
    ) -> dict[str, Any]:
        flow = self.repository.get_flow(flow_id)
        if not flow:
            raise ValueError(f"Task flow not found: {flow_id}")

        if flow.get("is_enabled") is False:
            raise ValueError(f"Task flow is disabled: {flow_id}")

        start_node = self.repository.get_start_node(flow_id)
        if not start_node:
            raise ValueError(f"Start node not found for flow: {flow_id}")

        return self.repository.create_session(
            organization_id=organization_id,
            session_id=session_id,
            flow_id=flow_id,
            current_node_key=start_node["node_key"],
            variables={},
        )

    def _run_session(
        self,
        task_session: dict[str, Any],
        user_message: str,
    ) -> TaskRunResponse:
        flow_id = task_session["flow_id"]
        task_session_id = task_session["id"]

        current_node_key = task_session["current_node_key"]
        variables = task_session.get("variables") or {}

        # waiting_user_input 상태일 때만 이번 사용자 메시지를 현재 Ask Node 입력으로 사용한다.
        user_input_for_current_node = (
            user_message
            if task_session.get("status") == "waiting_user_input"
            else None
        )

        max_steps = 20

        for _ in range(max_steps):
            node = self.repository.get_node_by_key(
                flow_id=flow_id,
                node_key=current_node_key,
            )

            if not node:
                return self._mark_failed(
                    task_session_id=task_session_id,
                    flow_id=flow_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    error={
                        "code": "NODE_NOT_FOUND",
                        "message": f"Node not found: {current_node_key}",
                    },
                )

            memory = TaskMemory(variables)

            executor_result = self._execute_node(
                node=node,
                memory=memory,
                user_message=user_input_for_current_node,
                is_waiting_input=user_input_for_current_node is not None,
            )

            memory.update(executor_result.memory_updates)
            variables = memory.to_dict()

            if executor_result.next_behavior == "wait_user":
                self.repository.update_session(
                    task_session_id,
                    {
                        "current_node_key": current_node_key,
                        "waiting_node_key": current_node_key,
                        "variables": variables,
                        "status": "waiting_user_input",
                    },
                )

                return TaskRunResponse(
                    handled=True,
                    message=executor_result.message,
                    status="waiting_user_input",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                )

            if executor_result.next_behavior == "complete":
                self.repository.update_session(
                    task_session_id,
                    {
                        "current_node_key": current_node_key,
                        "waiting_node_key": None,
                        "variables": variables,
                        "status": "completed",
                    },
                )

                return TaskRunResponse(
                    handled=True,
                    message=executor_result.message,
                    status="completed",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                )

            if executor_result.next_behavior == "handoff":
                self.repository.update_session(
                    task_session_id,
                    {
                        "current_node_key": current_node_key,
                        "waiting_node_key": None,
                        "variables": variables,
                        "status": "handoff",
                    },
                )

                return TaskRunResponse(
                    handled=True,
                    message=executor_result.message,
                    status="handoff",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                )

            if executor_result.next_behavior == "fail":
                edges = self.repository.list_edges_from(
                    flow_id=flow_id,
                    source_node_key=current_node_key,
                )

                failure_edge = select_failure_edge(edges)

                if failure_edge:
                    current_node_key = failure_edge["target_node_key"]

                    self.repository.update_session(
                        task_session_id,
                        {
                            "current_node_key": current_node_key,
                            "waiting_node_key": None,
                            "variables": variables,
                            "status": "running",
                            "last_error": executor_result.error,
                        },
                    )

                    user_input_for_current_node = None
                    continue

                return self._mark_failed(
                    task_session_id=task_session_id,
                    flow_id=flow_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    error=executor_result.error
                    or {
                        "code": "NODE_EXECUTION_FAILED",
                        "message": "Node execution failed.",
                    },
                    message=executor_result.message,
                )

            edges = self.repository.list_edges_from(
                flow_id=flow_id,
                source_node_key=current_node_key,
            )

            next_edge = select_next_edge(
                edges=edges,
                variables=variables,
            )

            if not next_edge:
                return self._mark_failed(
                    task_session_id=task_session_id,
                    flow_id=flow_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    error={
                        "code": "NEXT_EDGE_NOT_FOUND",
                        "message": f"No valid edge from node: {current_node_key}",
                    },
                    message=executor_result.message,
                )

            current_node_key = next_edge["target_node_key"]

            self.repository.update_session(
                task_session_id,
                {
                    "current_node_key": current_node_key,
                    "waiting_node_key": None,
                    "variables": variables,
                    "status": "running",
                },
            )

            # 사용자 입력은 현재 waiting node에서 한 번만 소비한다.
            user_input_for_current_node = None

            # Message Node의 message가 있으면 일단 사용자에게 반환한다.
            # 다음 노드까지 계속 자동 진행하면 메시지가 여러 개 합쳐질 수 있으므로
            # MVP 2단계에서는 message가 있는 노드는 한 번 응답하고 멈춘다.
            if executor_result.message:
                return TaskRunResponse(
                    handled=True,
                    message=executor_result.message,
                    status="running",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                )

        return self._mark_failed(
            task_session_id=task_session_id,
            flow_id=flow_id,
            current_node_key=current_node_key,
            variables=variables,
            error={
                "code": "MAX_STEPS_EXCEEDED",
                "message": "Task runner exceeded max steps.",
            },
        )

    def _execute_node(
        self,
        node: dict[str, Any],
        memory: TaskMemory,
        user_message: str | None,
        is_waiting_input: bool,
    ) -> ExecutorResult:
        node_type = node.get("node_type")

        executor = EXECUTOR_MAP.get(node_type)
        if not executor:
            return ExecutorResult(
                status="failed",
                message=f"아직 지원하지 않는 노드 타입입니다: {node_type}",
                next_behavior="fail",
                error={
                    "code": "UNSUPPORTED_NODE_TYPE",
                    "message": f"Unsupported node type: {node_type}",
                },
            )

        return executor(
            node=node,
            memory=memory,
            user_message=user_message,
            is_waiting_input=is_waiting_input,
        )

    def _mark_failed(
        self,
        task_session_id: str,
        flow_id: str,
        current_node_key: str,
        variables: dict[str, Any],
        error: dict[str, Any],
        message: str | None = None,
    ) -> TaskRunResponse:
        self.repository.update_session(
            task_session_id,
            {
                "current_node_key": current_node_key,
                "waiting_node_key": None,
                "variables": variables,
                "status": "failed",
                "last_error": error,
            },
        )

        return TaskRunResponse(
            handled=True,
            message=message or "태스크 실행 중 문제가 발생했습니다.",
            status="failed",
            flow_id=flow_id,
            task_session_id=task_session_id,
            current_node_key=current_node_key,
            variables=variables,
            error=error,
        )