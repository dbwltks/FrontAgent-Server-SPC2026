from typing import Any, Callable

from app.tasks.edge_evaluator import evaluate_condition_expression, select_failure_edge, select_next_edge
from app.tasks.executors import EXECUTOR_MAP
from app.tasks.memory import TaskMemory
from app.tasks.repository import TaskRepository
from app.tasks.types import ExecutorResult, TaskRunResponse, normalize_task_error


class DynamicTaskRunner:
    def __init__(self, repository: TaskRepository | None = None):
        self.repository = repository or TaskRepository()

    async def run(
        self,
        organization_id: str,
        session_id: str,
        user_message: str,
        flow_id: str | None = None,
        on_trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> TaskRunResponse:
        """
        MVP 2단계 기준 실행 방식.

        1. 진행 중 task_session이 있으면 이어서 실행
        2. 없으면 flow_id가 전달된 경우 해당 flow를 수동 시작
        3. flow_id가 없고 진행 중 session도 없으면 handled=False 반환

        trigger 판단은 agent_node에서 task_flows 트리거 매칭으로 수행하고,
        매칭 시 flow_id를 넘겨 세션을 시작한다.
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

        return await self._run_session(
            task_session=task_session,
            user_message=user_message,
            organization_id=organization_id,
            on_trace=on_trace,
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

    async def _run_session(
        self,
        task_session: dict[str, Any],
        user_message: str,
        organization_id: str,
        on_trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> TaskRunResponse:
        flow_id = task_session["flow_id"]
        task_session_id = task_session["id"]
        session_id = task_session.get("session_id", "")

        current_node_key = task_session["current_node_key"]
        variables = task_session.get("variables") or {}

        # waiting_user_input 상태일 때만 이번 사용자 메시지를 현재 노드 입력으로 사용한다.
        # instruction 노드는 매 턴 사용자 메시지를 보고 직접 판단하므로 항상 전달한다.
        was_waiting_for_input = task_session.get("status") == "waiting_user_input"

        max_steps = 20

        # 이번 턴에 거쳐간 노드 경로. 루프 안에서 그 자리에서 채우므로
        # 별도 DB 조회 없이 대화 로그(metadata)에 그대로 실어 보낼 수 있다.
        trace: list[dict[str, Any]] = []
        response_messages: list[str] = []

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
                    trace=trace,
                )

            # instruction 노드는 보통 사용자 메시지를 봐야만 슬롯 충족 여부를
            # 판단할 수 있어 매 턴 LLM을 호출한다. 하지만 "이 슬롯 자체가 이
            # service_item에 필요 없다"처럼 사용자 메시지와 무관하게 코드로
            # 바로 결정되는 케이스(branch_condition이 채워진 경우)는 LLM 호출
            # 없이 곧장 다음 노드로 넘어간다 - LLM 판단에 맡기면(예: instruction
            # 텍스트로 "필요 없으면 스킵해라"를 지시) 지시를 놓치는 사례가
            # 실측됐다(예약 인원수가 무의미한 청소 서비스에도 매번 물어봄).
            #
            # branch_condition이 빈 문자열인 instruction 노드가 압도적으로
            # 많고(대부분의 ask_* 노드), evaluate_condition_expression은 빈
            # 조건을 항상 True로 취급하므로 반드시 조건이 채워진 경우에만
            # 이 사전 평가를 적용한다 - 그렇지 않으면 모든 instruction 노드가
            # LLM 호출 없이 즉시 branch_node_key로 건너뛰는 회귀가 생긴다.
            node_config = node.get("config") or {}
            pre_branch_condition = node_config.get("branch_condition")
            if (
                node.get("node_type") == "instruction"
                and self._get_next_step_mode(node) == "branch"
                and pre_branch_condition
                and pre_branch_condition.strip()
                and evaluate_condition_expression(pre_branch_condition, variables)
            ):
                next_node_key = node_config.get("branch_node_key")
                if self._node_key_exists(flow_id, next_node_key):
                    trace.append(
                        {
                            "node_key": current_node_key,
                            "node_label": node.get("label"),
                            "node_type": node.get("node_type"),
                            "status": "skipped",
                            "next_behavior": "evaluate_edges",
                            "memory_updates": [],
                            "error": None,
                        }
                    )
                    current_node_key = next_node_key
                    continue

            user_input_for_current_node = (
                user_message
                if was_waiting_for_input or node.get("node_type") == "instruction"
                else None
            )

            memory = TaskMemory(variables)

            executor_result = await self._execute_node(
                node=node,
                memory=memory,
                user_message=user_input_for_current_node,
                is_waiting_input=user_input_for_current_node is not None,
                organization_id=organization_id,
            )

            memory.update(executor_result.memory_updates)
            variables = memory.to_dict()

            trace_item = {
                "node_key": current_node_key,
                "node_label": node.get("label"),
                "node_type": node.get("node_type"),
                "status": executor_result.status,
                "next_behavior": executor_result.next_behavior,
                "memory_updates": list(executor_result.memory_updates.keys()),
                "error": executor_result.error,
            }
            trace.append(trace_item)
            if on_trace:
                on_trace({**trace_item, "index": len(trace)})

            if executor_result.message:
                response_messages.append(executor_result.message)

            if executor_result.next_behavior == "wait_user":
                self.repository.update_session(
                    task_session_id,
                    {
                        "current_node_key": current_node_key,
                        "waiting_node_key": current_node_key,
                        "variables": variables,
                        "status": "waiting_user_input",
                    },
                organization_id=organization_id,
                    session_id=session_id,
                )

                return TaskRunResponse(
                    handled=True,
                    message="\n".join(response_messages) if response_messages else executor_result.message,
                    status="waiting_user_input",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    trace=trace,
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
                organization_id=organization_id,
                    session_id=session_id,
                )

                return TaskRunResponse(
                    handled=True,
                    message="\n".join(response_messages) if response_messages else executor_result.message,
                    status="completed",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    trace=trace,
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
                organization_id=organization_id,
                    session_id=session_id,
                )

                return TaskRunResponse(
                    handled=True,
                    message="\n".join(response_messages) if response_messages else executor_result.message,
                    status="handoff",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    trace=trace,
                )

            if executor_result.next_behavior == "fail":
                normalized_error = normalize_task_error(
                    executor_result.error,
                    code="NODE_EXECUTION_FAILED",
                    message="Node execution failed.",
                    node_key=current_node_key,
                    node_type=node.get("node_type"),
                )

                failure_node_key = self._move_to_failure_edge_if_exists(
                    task_session_id=task_session_id,
                    flow_id=flow_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    error=normalized_error,
                )

                if failure_node_key:
                    current_node_key = failure_node_key
                    was_waiting_for_input = False
                    continue

                return self._mark_failed(
                    task_session_id=task_session_id,
                    flow_id=flow_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    error=normalized_error,
                    message=executor_result.message,
                    trace=trace,
                )

            if self._is_terminal_node_config(node):
                self.repository.update_session(
                    task_session_id,
                    {
                        "current_node_key": current_node_key,
                        "waiting_node_key": None,
                        "variables": variables,
                        "status": "completed",
                    },
                organization_id=organization_id,
                    session_id=session_id,
                )

                return TaskRunResponse(
                    handled=True,
                    message=executor_result.message,
                    status="completed",
                    flow_id=flow_id,
                    task_session_id=task_session_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    trace=trace,
                )

            next_node_key = self._resolve_next_node_key(
                node=node,
                flow_id=flow_id,
                current_node_key=current_node_key,
                variables=variables,
            )

            if not next_node_key:
                normalized_error = normalize_task_error(
                    {
                        "code": "NEXT_NODE_NOT_FOUND",
                        "message": f"No valid next node from node: {current_node_key}",
                    },
                    node_key=current_node_key,
                    node_type=node.get("node_type"),
                )

                return self._mark_failed(
                    task_session_id=task_session_id,
                    flow_id=flow_id,
                    current_node_key=current_node_key,
                    variables=variables,
                    error=normalized_error,
                    message=executor_result.message,
                    trace=trace,
                )

            current_node_key = next_node_key

            self.repository.update_session(
                task_session_id,
                {
                    "current_node_key": current_node_key,
                    "waiting_node_key": None,
                    "variables": variables,
                    "status": "running",
                },
            organization_id=organization_id,
                    session_id=session_id,
                )

            # 사용자 입력은 현재 waiting node에서 한 번만 소비한다.
            was_waiting_for_input = False

            continue

        return self._mark_failed(
            task_session_id=task_session_id,
            flow_id=flow_id,
            current_node_key=current_node_key,
            variables=variables,
            error={
                "code": "MAX_STEPS_EXCEEDED",
                "message": "Task runner exceeded max steps.",
            },
            trace=trace,
        )

    def _get_next_step_mode(self, node: dict[str, Any]) -> str:
        config = node.get("config") or {}
        mode = config.get("next_step_mode")
        return mode if mode in {"single", "branch", "end"} else "single"

    def _is_terminal_node_config(self, node: dict[str, Any]) -> bool:
        return self._get_next_step_mode(node) == "end"

    def _resolve_next_node_key(
        self,
        node: dict[str, Any],
        flow_id: str,
        current_node_key: str,
        variables: dict[str, Any],
    ) -> str | None:
        config = node.get("config") or {}
        mode = self._get_next_step_mode(node)

        if mode == "single":
            next_node_key = config.get("next_node_key")
            if self._node_key_exists(flow_id, next_node_key):
                return next_node_key

        if mode == "branch":
            condition_matched = evaluate_condition_expression(
                config.get("branch_condition"),
                variables,
            )
            next_node_key = config.get("branch_node_key") if condition_matched else config.get("fallback_node_key")
            if self._node_key_exists(flow_id, next_node_key):
                return next_node_key

        edges = self.repository.list_edges_from(
            flow_id=flow_id,
            source_node_key=current_node_key,
        )
        next_edge = select_next_edge(
            edges=edges,
            variables=variables,
        )
        return next_edge["target_node_key"] if next_edge else None

    def _node_key_exists(self, flow_id: str, node_key: str | None) -> bool:
        if not node_key:
            return False
        return self.repository.get_node_by_key(flow_id=flow_id, node_key=node_key) is not None

    async def _execute_node(
        self,
        node: dict[str, Any],
        memory: TaskMemory,
        user_message: str | None,
        is_waiting_input: bool,
        organization_id: str,
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
                    "node_type": node_type,
                },
            )

        # instruction executor만 OpenAI 호출이 있는 async 함수다.
        # 나머지(message/condition/function)는 동기 함수이므로 그대로 호출한다.
        if node_type == "instruction":
            return await executor(
                node=node,
                memory=memory,
                user_message=user_message,
                is_waiting_input=is_waiting_input,
                organization_id=organization_id,
            )

        return executor(
            node=node,
            memory=memory,
            user_message=user_message,
            is_waiting_input=is_waiting_input,
            organization_id=organization_id,
        )
    
    def _move_to_failure_edge_if_exists(
        self,
        task_session_id: str,
        flow_id: str,
        current_node_key: str,
        variables: dict[str, Any],
        error: dict[str, Any],
    ) -> str | None:
        edges = self.repository.list_edges_from(
            flow_id=flow_id,
            source_node_key=current_node_key,
        )

        failure_edge = select_failure_edge(edges)

        if not failure_edge:
            return None

        next_node_key = failure_edge["target_node_key"]

        self.repository.update_session(
            task_session_id,
            {
                "current_node_key": next_node_key,
                "waiting_node_key": None,
                "variables": variables,
                "status": "running",
                "last_error": error,
            },
        )

        return next_node_key
    

    def _mark_failed(
        self,
        task_session_id: str,
        flow_id: str,
        current_node_key: str,
        variables: dict[str, Any],
        error: dict[str, Any],
        message: str | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> TaskRunResponse:
        normalized_error = normalize_task_error(
            error,
            code="TASK_FAILED",
            message="Task failed.",
            node_key=current_node_key,
        )

        self.repository.update_session(
            task_session_id,
            {
                "current_node_key": current_node_key,
                "waiting_node_key": None,
                "variables": variables,
                "status": "failed",
                "last_error": normalized_error,
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
            error=normalized_error,
            trace=trace or [],
        )
