from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.graph.streaming_runner import run_agent_streaming


router = APIRouter(tags=["WebSocket Chat"])


@router.websocket("/ws/chat/{organization_id}/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    organization_id: str,
    session_id: str,
):
    """
    사용자 채팅 위젯용 WebSocket 엔드포인트.

    역할:
    - 사용자 메시지를 WebSocket으로 받는다.
    - AI 자동응답이 켜져 있으면 AI 응답을 streaming한다.
    - AI 자동응답이 꺼져 있으면 고객 메시지만 저장하고 관리자 응답 대기 상태를 보낸다.
    """

    # 1. WebSocket 연결 수락
    await websocket.accept()

    # 2. 연결 성공 이벤트 전송
    await websocket.send_json(
        {
            "type": "connected",
            "organization_id": organization_id,
            "session_id": session_id,
        }
    )

    try:
        while True:
            # 3. 사용자 메시지 수신
            data = await websocket.receive_json()
            user_message = data.get("message")

            # 4. message가 없으면 에러 반환
            if not user_message:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "message is required",
                    }
                )
                continue

            # 5. 사용자 메시지 수신 확인 이벤트
            await websocket.send_json(
                {
                    "type": "user_message_received",
                    "message": user_message,
                }
            )

            # 6. AI 응답 시작 이벤트를 중복으로 보내지 않기 위한 플래그
            #    실제 delta가 처음 생성될 때 ai_response_start를 보낸다.
            ai_response_started = False

            async def send_delta(delta: str) -> None:
                """
                OpenAI에서 생성된 delta를 클라이언트로 전송한다.

                첫 delta가 오기 직전에 ai_response_start 이벤트를 보낸다.
                이렇게 하면 ai_enabled=false인 경우에는 ai_response_start가 잘못 나가지 않는다.
                """

                nonlocal ai_response_started

                if not ai_response_started:
                    await websocket.send_json(
                        {
                            "type": "ai_response_start",
                        }
                    )
                    ai_response_started = True

                await websocket.send_json(
                    {
                        "type": "ai_response_delta",
                        "delta": delta,
                    }
                )

            try:
                # 7. Streaming Agent 실행
                #    내부 흐름:
                #    - conversation 생성/조회
                #    - customer message 저장
                #    - ai_enabled 확인
                #    - AI가 켜져 있으면 rule/rag/response streaming 실행
                #    - 최종 응답 저장
                result = await run_agent_streaming(
                    initial_state={
                        "organization_id": organization_id,
                        "session_id": session_id,
                        "user_message": user_message,

                        # conversation_node에서 실제 conversation_id를 채운다.
                        "conversation_id": None,

                        # 기본값은 True지만 conversation_node에서 DB 값을 다시 넣는다.
                        "ai_enabled": True,

                        # 초기 state 값들
                        "session_state": {},
                        "intent": None,
                        "rules": [],
                        "applied_rules": [],
                        "knowledge_context": [],
                        "used_knowledge": [],
                        "final_response": None,
                    },
                    on_delta=send_delta,
                )

                # 8. AI 자동응답이 꺼져 있으면 AI 응답 완료 이벤트를 보내지 않는다.
                #    고객 메시지는 이미 conversation_node에서 저장된 상태다.
                if not result.get("ai_enabled", True):
                    await websocket.send_json(
                        {
                            "type": "ai_disabled",
                            "message": "AI 자동응답이 꺼져 있어 관리자 응답을 기다립니다.",
                            "conversation_id": result.get("conversation_id"),
                        }
                    )

                    await websocket.send_json(
                        {
                            "type": "done",
                        }
                    )

                    continue

                # 9. AI 응답 완료 이벤트
                await websocket.send_json(
                    {
                        "type": "ai_response_done",
                        "message": result.get("final_response"),
                        "intent": result.get("intent"),
                        "conversation_id": result.get("conversation_id"),
                        "applied_rules": result.get("applied_rules", []),
                        "used_knowledge": result.get("used_knowledge", []),
                    }
                )

                # 10. 전체 처리 완료 이벤트
                await websocket.send_json(
                    {
                        "type": "done",
                    }
                )

            except Exception as e:
                # 11. Agent 실행 중 에러가 나도 WebSocket 연결은 유지한다.
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Agent streaming failed: {str(e)}",
                    }
                )

                await websocket.send_json(
                    {
                        "type": "done",
                    }
                )

    except WebSocketDisconnect:
        print(f"WebSocket disconnected: {organization_id}/{session_id}")