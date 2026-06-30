# Front Agent 웹 채팅 / 웹 음성방 개선 정리

## 1. 최종 방향

기존 프로젝트를 완전히 새로 만들기보다는, 기존에 만든 **Agent Core**는 유지하고, **웹 음성방(`web_call`)만 실시간 구조로 새로 개선**하는 방향이 가장 좋다.

핵심은 다음과 같다.

> 기존 LangGraph, Rules, Knowledge, Task, Conversation 구조는 유지한다.
> 대신 음성방은 기존 `/voice` 업로드 방식이 아니라, 새 `web_call` WebSocket 구조로 만든다.
> 현재 단계에서는 전화(`phone_call`)는 제외하고, 웹 기준으로 `web_chat`, `web_call` 두 채널만 운영한다.

---

## 2. 최종 채널 정책

### 2.1 web_chat

`web_chat`은 일반 채팅방이다.

- 사용자 입력: 채팅 텍스트
- AI 출력: 채팅 텍스트
- STT 사용 안 함
- TTS 사용 안 함

흐름은 다음과 같다.

text → Agent → text

예시:

고객: 오늘 예약 가능해요?
AI: 네, 오늘 오후 3시와 5시 가능합니다.

---

### 2.2 web_call

`web_call`은 웹 음성방이다.

- 사용자 입력: 음성 또는 채팅 텍스트
- AI 출력: 항상 텍스트 + 음성
- 음성 입력일 때 STT 사용
- AI 답변마다 TTS 사용

흐름은 다음과 같다.

음성 입력:

voice → STT → Agent → text + TTS

채팅 입력:

text → Agent → text + TTS

예시:

고객: 🎙️ 오늘 예약 가능해요?
AI: 🔊💬 네, 오늘 오후 3시와 5시 가능합니다.

고객: 💬 가격도 알려주세요
AI: 🔊💬 기본 상담은 30분 50,000원입니다.

즉, `web_call`에서는 사용자가 음성으로 물어봐도 되고, 채팅으로 물어봐도 된다.
다만 AI 답변은 항상 음성과 텍스트가 같이 나간다.

---

## 3. 기존 프로젝트에서 유지할 것

현재 프로젝트에서 이미 잘 만들어진 핵심 구조는 유지한다.

유지할 항목은 다음과 같다.

- LangGraph Agent 구조
- Decision Node
- Rule Node
- Knowledge Node
- Task Node
- Response Node
- Conversation 저장
- Agent Run Log
- Organization AI Settings
- Reservation / Service / Product 도메인 API

현재 Agent Graph 구조는 다음과 같다.

conversation
→ active task 확인
→ decision 또는 task_router
→ rule
→ task / knowledge / response
→ save
→ log

이 구조는 정확한 상담 처리, 태스크 관리, 상담 기록 저장에는 좋다.

다만 이 구조를 실시간 음성의 첫 응답 경로로 그대로 사용하면 느릴 수 있다.
따라서 `web_call`에는 별도의 실시간 레이어와 fast path가 필요하다.

---

## 4. 기존 구조의 문제점

### 4.1 기존 /voice는 진짜 실시간 구조가 아님

현재 `/voice/turn` 구조는 대략 다음과 같다.

오디오 파일 업로드
→ 전체 오디오 읽기
→ STT
→ Agent 실행
→ TTS 전체 생성
→ base64 audio 반환

이 방식은 실제 실시간 음성 대화라기보다, 음성 파일을 보내고 답변을 받는 구조에 가깝다.

문제점은 다음과 같다.

- 사용자가 말하는 중 partial transcript 표시가 어렵다.
- 오디오 chunk 실시간 전송이 어렵다.
- AI 답변 중 사용자 끼어들기 처리가 어렵다.
- ElevenLabs 데모처럼 즉각적인 대화 느낌이 약하다.

---

### 4.2 /voice/pipeline/turn/stream도 입력은 아직 업로드 기반

현재 pipeline stream은 응답을 SSE로 스트리밍하지만, 시작은 여전히 업로드 기반이다.

오디오 전체 업로드
→ STT 완료
→ Agent 실행
→ TTS segment streaming

좋은 점은 다음과 같다.

- AI 답변을 delta로 받을 수 있다.
- 문장 단위 TTS streaming 구조가 일부 있다.

부족한 점은 다음과 같다.

- 입력 음성은 실시간 chunk가 아니다.
- STT partial transcript가 없다.
- WebSocket 기반 양방향 제어가 아니다.
- barge-in 처리가 부족하다.

---

### 4.3 한 턴에 LLM을 2번 부를 수 있음

현재 일반적인 흐름은 다음과 같다.

Decision LLM
→ Response LLM

즉, 사용자 질문 하나에 LLM 호출이 최소 1~2번 들어갈 수 있다.
채팅에서는 괜찮을 수 있지만, 음성에서는 이 구조가 느리게 느껴질 수 있다.

---

### 4.4 DB / checkpointer 의존이 큼

현재 구조는 안정적이지만 매 턴마다 여러 작업이 붙는다.

- conversation 조회/생성
- active task 조회
- rule 조회
- knowledge 검색
- response 생성
- AI 메시지 저장
- agent run 저장
- Postgres checkpointer

이 구조는 채팅과 관리자 기록에는 좋다.
하지만 실시간 음성의 첫 응답 속도에는 부담이 될 수 있다.

---

## 5. 개선 방향

핵심 개선 방향은 다음과 같다.

기존 full graph는 유지한다.
web_call에는 빠른 realtime layer를 추가한다.
복잡한 요청은 기존 graph로 fallback한다.
단순 요청이나 태스크 단계는 fast path로 처리한다.

전체 흐름은 다음과 같다.

web_chat
→ 기존 /chat 유지

web_call
→ 신규 WebSocket
→ STT streaming
→ fast router
→ Agent Runtime
→ TTS streaming
→ 텍스트 + 음성 동시 출력

---

## 6. 새로 만들 구조

추천 폴더 구조는 다음과 같다.

app/
  api/
    chat.py
    web_call.py

  services/
    agent_runtime.py

    web_call/
      **init**.py
      protocol.py
      session_manager.py
      stt_stream.py
      tts_stream.py
      agent_bridge.py
      audio_buffer.py
      interruption.py

각 파일 역할은 다음과 같다.

### app/api/web_call.py

- WebSocket 엔드포인트
- web_call 연결 관리
- 클라이언트 이벤트 수신
- 서버 이벤트 전송

### app/services/agent_runtime.py

- 기존 LangGraph 실행을 감싸는 공통 함수
- web_chat / web_call 모두 사용

### app/services/web_call/protocol.py

- WebSocket 이벤트 타입 정의

### app/services/web_call/session_manager.py

- web_call 세션 상태 관리
- 현재 응답 중인지, 사용자가 말하는 중인지 관리

### app/services/web_call/stt_stream.py

- 음성 chunk를 STT 서비스로 전달
- partial / final transcript 수신

### app/services/web_call/tts_stream.py

- AI 텍스트 delta를 TTS로 변환
- audio chunk 생성

### app/services/web_call/agent_bridge.py

- STT 결과 또는 채팅 입력을 Agent Runtime으로 전달

### app/services/web_call/audio_buffer.py

- audio chunk 순서, 버퍼 관리

### app/services/web_call/interruption.py

- 사용자가 AI 답변 중 끼어들었을 때 처리

---

## 7. WebSocket 이벤트 설계

### 7.1 Client → Server

음성 chunk 전송

type: audio_chunk
audio: base64 audio data

채팅 입력

type: text_message
text: 가격도 알려주세요

끼어들기

type: interrupt

통화 종료

type: call_end

---

### 7.2 Server → Client

STT 중간 결과

type: partial_transcript
text: 오늘 예약 가능한

STT 최종 결과

type: final_transcript
text: 오늘 예약 가능한 시간 있어요?

AI 텍스트 스트리밍

type: assistant_text_delta
text: 네,

AI 음성 chunk

type: assistant_audio_chunk
audio: base64 audio data

AI 답변 완료

type: assistant_message_done
message: 네, 오늘 오후 3시와 5시 가능합니다.

음성 출력 완료

type: audio_end

---

## 8. DB 수정 방향

### 8.1 conversations

conversations 테이블에는 다음 필드를 사용한다.

- id
- organization_id
- session_id
- channel
- mode
- status
- created_at
- updated_at

정책은 다음과 같다.

- web_chat → mode = text
- web_call → mode = voice

channel 값은 현재 단계에서 다음 두 개만 사용한다.

- web_chat
- web_call

---

### 8.2 messages

messages 테이블에는 다음 필드를 사용한다.

- id
- conversation_id
- role
- content
- input_type
- output_type
- audio_url
- metadata
- created_at

메시지 저장 정책은 다음과 같다.

web_chat 사용자 메시지:

- input_type = text

web_chat AI 메시지:

- output_type = text

web_call 사용자 음성:

- input_type = voice
- content = STT final transcript

web_call 사용자 채팅:

- input_type = text

web_call AI 메시지:

- output_type = text_and_voice
- content = AI 답변 텍스트
- audio_url = 선택

---

## 9. 공통 Agent Runtime 분리

기존 `/chat`, 기존 `/voice`, 신규 `/web_call`이 모두 같은 Agent 실행 함수를 호출하게 만든다.

공통 함수 개념:

async def run_agent_turn(
organization_id,
session_id,
message,
channel,
log_message = None
)

사용 예시:

web_chat:

run_agent_turn(
organization_id = org_id,
session_id = session_id,
message = user_text,
channel = "web_chat"
)

web_call 음성 입력:

run_agent_turn(
organization_id = org_id,
session_id = session_id,
message = final_transcript,
log_message = final_transcript,
channel = "web_call"
)

web_call 채팅 입력:

run_agent_turn(
organization_id = org_id,
session_id = session_id,
message = user_text,
channel = "web_call"
)

출력 정책:

web_call이면 output_type은 text_and_voice
web_chat이면 output_type은 text

---

## 10. 속도 개선 핵심

### 10.1 Decision LLM 호출 줄이기

현재는 매 턴 `decision_node`에서 LLM을 호출할 수 있다.

개선 방향은 다음과 같다.

fast_router 먼저 실행
→ 명확하면 decision LLM 생략
→ 애매하면 기존 decision_node 사용

fast_router가 처리할 수 있는 예시는 다음과 같다.

- 인사
- 예약 요청
- 가격 문의
- 종료 요청
- 상담사 연결 요청

효과는 다음과 같다.

- LLM 호출 2번 → 1번으로 감소
- 첫 응답 속도 개선
- 비용 감소

---

### 10.2 단순 응답은 LLM 생략

아래 문장들은 LLM을 거치지 않고 템플릿으로 바로 응답할 수 있다.

- 안녕하세요, 무엇을 도와드릴까요?
- 확인해볼게요.
- 성함을 알려주세요.
- 연락처를 알려주세요.
- 상담사에게 연결해드릴게요.

효과는 다음과 같다.

- 태스크 진행 속도 향상
- 예약 플로우 안정성 증가
- 음성 응답 지연 감소

---

### 10.3 web_call에서는 먼저 짧게 반응

음성방에서는 긴 작업 전에 먼저 짧게 반응해야 한다.

예시:

사용자: 오늘 5시 예약 가능해요?

AI 즉시:
확인해볼게요.

뒤에서:
예약 가능 시간 조회

AI 이어서:
오늘 5시는 가능합니다. 성함을 알려주세요.

이 구조가 실제 전화 상담처럼 느껴진다.

---

### 10.4 저장 / 로그는 비동기 처리

실시간 응답을 막지 말고 저장과 로그는 뒤로 미룬다.

먼저 응답 스트리밍
→ 뒤에서 message 저장
→ 뒤에서 agent_run 저장
→ 뒤에서 usage log 저장

특히 `web_call`에서는 DB 저장 실패 때문에 대화가 멈추면 안 된다.

---

### 10.5 세션 상태 캐시

`web_call`에서는 매 턴 DB에서 모든 정보를 가져오지 말고 Redis 또는 memory cache를 사용한다.

캐시 대상은 다음과 같다.

- conversation_id
- active_task
- current_task_node
- recent_messages
- ai_enabled
- organization voice settings

MVP에서는 memory cache로 시작 가능하다.
운영에서는 Redis를 추천한다.

---

## 11. Pipeline vs Realtime 결정

### 11.1 현재 단계에서는 Pipeline 메인 추천

현재 프로젝트에는 규칙, 지식, 태스크, 예약, 관리자 기록이 중요하다.
그래서 처음에는 직접 제어 가능한 pipeline이 적합하다.

web_call pipeline 흐름:

audio chunk
→ streaming STT
→ Agent Runtime
→ streaming TTS
→ text + audio 출력

장점:

- 기존 코드 재사용 가능
- RAG / Rules / Task 제어 쉬움
- ElevenLabs TTS 붙이기 쉬움
- 조직별 STT / TTS 교체 가능
- 상담 기록 저장 쉬움

단점:

- Realtime API보다 구현량 많음
- barge-in 직접 구현 필요
- 초기에는 데모보다 느릴 수 있음

---

### 11.2 Realtime은 나중에 고급 모드로 유지

voice_mode는 다음 두 가지로 나눌 수 있다.

- pipeline
- realtime

기본값은 pipeline으로 둔다.

나중에 고급 옵션으로 realtime을 제공할 수 있다.
현재는 phone_call을 제외하고 웹만 진행하므로, `web_call` pipeline WebSocket을 먼저 완성하는 것이 좋다.

---

## 12. 기존 API 정리 방향

### 12.1 유지

/chat

- web_chat 전용
- text in / text out
- SSE streaming 유지

---

### 12.2 유지하되 fallback으로 사용

/voice/turn
/voice/pipeline/turn/stream

- 기존 테스트용
- 파일 업로드 기반 fallback
- 추후 deprecated 가능

---

### 12.3 신규 추가

/web-call/ws

- web_call 전용
- voice input 가능
- text input 가능
- output은 text_and_voice

---

## 13. 개발 순서

### 1단계. 채널 정책 정리

- phone_call 제거
- channel enum을 web_chat / web_call 중심으로 정리
- web_call output_type은 text_and_voice로 고정

---

### 2단계. agent_runtime.py 분리

`chat.py`, `voice_pipeline.py`, 새 `web_call.py`가 공통으로 쓸 함수를 만든다.

필요 함수:

- run_agent_turn
- stream_agent_turn

---

### 3단계. /web-call/ws 만들기

처음에는 음성 없이 텍스트만 먼저 처리한다.

client text_message
→ Agent
→ assistant_text_delta
→ TTS 생성
→ assistant_audio_chunk

이 단계를 먼저 성공시킨다.

---

### 4단계. TTS streaming 붙이기

현재 `voice_pipeline.py`의 문장 단위 TTS 로직을 재사용한다.

LLM delta
→ 문장 단위 split
→ TTS 생성
→ audio chunk 전송

---

### 5단계. STT streaming 붙이기

audio_chunk
→ STT WebSocket
→ partial_transcript
→ final_transcript
→ Agent 실행

---

### 6단계. interruption 처리

AI 말하는 중 사용자 음성 입력
→ 현재 TTS 중단
→ 현재 응답 cancel 또는 ignore
→ 새 발화 우선 처리

---

### 7단계. fast_router 추가

명확한 인사 / 예약 / 가격 / 종료 / 상담사 연결은 LLM decision을 생략한다.
애매한 것만 기존 decision_node를 사용한다.

---

## 14. 최종 아키텍처

### web_chat

text input
→ Agent Runtime
→ text output

### web_call

voice input
→ STT
→ Agent Runtime
→ text output
→ TTS
→ audio output

text input
→ Agent Runtime
→ text output
→ TTS
→ audio output

전체 구조:

Frontend
├─ /chat
│ └─ web_chat
│ └─ text only
│
└─ /call
└─ web_call
├─ voice input
├─ text input
├─ transcript
├─ AI text
└─ AI audio

---

## 15. 최종 결론

기존 프로젝트 개선 방향은 다음과 같다.

1. phone_call은 제거하고 web_chat / web_call만 남긴다.
2. web_chat은 기존 /chat 구조를 유지한다.
3. web_call은 새 WebSocket 기반으로 만든다.
4. web_call에서는 음성 입력과 채팅 입력을 둘 다 허용한다.
5. web_call의 AI 응답은 항상 텍스트 + 음성으로 출력한다.
6. 기존 LangGraph, Rules, Knowledge, Task는 유지한다.
7. 다만 실시간 속도를 위해 fast_router와 템플릿 응답을 추가한다.
8. Decision LLM 호출을 줄이고, 저장/로그는 비동기로 미룬다.
9. pipeline을 기본으로 만들고, realtime은 나중에 옵션으로 둔다.

한 줄 요약:

> 기존 Agent Core는 살리고, `web_call`만 실시간 WebSocket 레이어로 새로 만든다.
> 속도 문제는 노드를 버리는 것이 아니라 fast path, 캐시, 비동기 저장, LLM 호출 최소화로 해결한다.
