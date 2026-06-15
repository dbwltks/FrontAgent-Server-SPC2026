import asyncio
import json

import websockets


async def main():
    """
    WebSocket streaming 채팅 테스트.

    실행 전 FastAPI 서버를 켜야 한다:
    python -m uvicorn app.main:app --reload --port 8001
    """

    organization_id = "org_test"
    session_id = "ws_stream_test_001"

    url = f"ws://localhost:8000/ws/chat/{organization_id}/{session_id}"

    async with websockets.connect(url) as websocket:
        # 1. connected 이벤트 수신
        first_message = await websocket.recv()
        print("SERVER:", first_message)

        # 2. 사용자 메시지 전송
        payload = {
            "message": "방문 상담은 얼마예요?"
        }

        await websocket.send(json.dumps(payload, ensure_ascii=False))
        print("CLIENT:", payload)

        # 3. streaming 응답 수신
        full_text = ""

        while True:
            response = await websocket.recv()
            data = json.loads(response)

            event_type = data.get("type")

            if event_type == "ai_response_delta":
                # delta는 AI 응답의 텍스트 조각이다.
                delta = data.get("delta", "")
                full_text += delta
                print(delta, end="", flush=True)

            else:
                # start, done, error 같은 이벤트는 줄바꿈해서 출력
                print("\nSERVER EVENT:", data)

            if event_type == "done":
                break

        print("\n\nFULL TEXT:", full_text)


if __name__ == "__main__":
    asyncio.run(main())