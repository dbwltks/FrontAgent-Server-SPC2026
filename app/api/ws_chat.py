from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.graph.streaming_runner import run_agent_streaming


router = APIRouter(tags=["WebSocket Chat"])


class DeltaSender:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.started = False

    async def __call__(self, delta: str) -> None:
        if not self.started:
            await self.websocket.send_json({"type": "ai_response_start"})
            self.started = True

        await self.websocket.send_json({"type": "ai_response_delta", "delta": delta})


@router.websocket("/ws/chat/{organization_id}/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    organization_id: str,
    session_id: str,
):
    await websocket.accept()

    await websocket.send_json(
        {
            "type": "connected",
            "organization_id": organization_id,
            "session_id": session_id,
        }
    )

    is_processing = False

    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("message")
            knowledge_folder_id = data.get("folder_id")

            if not user_message:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "message is required",
                    }
                )
                continue

            if is_processing:
                await websocket.send_json(
                    {
                        "type": "processing",
                        "message": "이전 답변을 생성 중입니다. 잠시 후 다시 시도해주세요.",
                    }
                )
                continue

            await websocket.send_json(
                {
                    "type": "user_message_received",
                    "message": user_message,
                }
            )

            is_processing = True
            send_delta = DeltaSender(websocket)

            async def send_trace_step(step: str, status: str, detail: str = "", items: list = []) -> None:
                await websocket.send_json(
                    {
                        "type": "trace_step",
                        "step": step,
                        "status": status,
                        "detail": detail,
                        "items": items,
                    }
                )

            try:
                result = await run_agent_streaming(
                    initial_state={
                        "organization_id": organization_id,
                        "session_id": session_id,
                        "user_message": user_message,
                        "conversation_id": None,
                        "ai_enabled": True,
                        "session_state": {},
                        "conversation_history": [],
                        "intent": None,
                        "next_action": None,
                        "task_type": None,
                        "use_knowledge": False,
                        "decision_reason": None,
                        "task_result": None,
                        "should_use_knowledge": False,
                        "rules": [],
                        "rule_instructions": "",
                        "applied_rules": [],
                        "knowledge_folder_id": knowledge_folder_id,
                        "knowledge_context": [],
                        "used_knowledge": [],
                        "final_response": None,
                    },
                    on_delta=send_delta,
                    on_trace_step=send_trace_step,
                )

                if not result.get("ai_enabled", True):
                    await websocket.send_json(
                        {
                            "type": "ai_disabled",
                            "message": "AI 자동응답이 꺼져 있어 관리자 응답을 기다립니다.",
                            "conversation_id": result.get("conversation_id"),
                        }
                    )
                    await websocket.send_json({"type": "done"})
                    continue

                await websocket.send_json(
                    {
                        "type": "ai_response_done",
                        "message": result.get("final_response"),
                        "intent": result.get("intent"),
                        "next_action": result.get("next_action"),
                        "task_type": result.get("task_type"),
                        "use_knowledge": result.get("use_knowledge", False),
                        "decision_reason": result.get("decision_reason"),
                        "conversation_id": result.get("conversation_id"),
                        "applied_rules": result.get("applied_rules", []),
                        "used_knowledge": result.get("used_knowledge", []),
                    }
                )

                await websocket.send_json({"type": "done"})

            except Exception as e:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Agent streaming failed: {str(e)}",
                    }
                )
                await websocket.send_json({"type": "done"})

            finally:
                is_processing = False

    except WebSocketDisconnect:
        is_processing = False
