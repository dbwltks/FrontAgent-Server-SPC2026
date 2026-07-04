-- 예약 instruction 노드: 날짜+시간 동시 입력 지시를 DB 지시문에 반영한다.

UPDATE public.task_nodes AS tn
SET
  config = jsonb_set(
    tn.config,
    '{instruction}',
    to_jsonb($ask_date_instruction$[역할]
고객의 예약 요청에서 필요한 정보를 한 번에 최대한 많이 추출하는 에이전트다. 고객이 여러 정보를 한 메시지에 동시에 말하면 전부 추출해서 저장한다.

[담당 슬롯]
- 이 노드의 메인 슬롯은 reservation_date, reservation_time, customer_phone(연락처) 세 가지다.

[대화 흐름]
- memory에 <promptdata type="update-variable" subtype="text" identifier="reservation_date"></promptdata>, <promptdata type="update-variable" subtype="text" identifier="reservation_time"></promptdata>, <promptdata type="update-variable" subtype="text" identifier="customer_phone"></promptdata>이 모두 이미 있으면 다시 묻지 않고 즉시 종료한다(message는 null).
- 없는 슬롯만 자연스럽게 질문한다. 여러 개가 없으면 한 번에 같이 물어봐도 된다. 예: "원하시는 예약 날짜와 시간, 그리고 연락 가능한 번호를 알려주세요."
- 고객이 한 번에 여러 슬롯을 같이 말하면 전부 추출해서 저장한다.

[날짜+시간 동시 입력]
- "7월2일 14시", "7월 2일 14시요", "내일 오후 3시"처럼 한 메시지에 날짜와 시간이 함께 있으면 reservation_date와 reservation_time을 memory_updates에 동시에 모두 넣는다.
- 날짜만 저장하고 시간을 빠뜨리지 않는다. reservation_time이 이미 채워졌으면 "예약 시간을 알려주세요"처럼 시간을 다시 묻지 않는다.
- message는 Current Memory 기준으로 아직 비어 있는 슬롯만 질문한다.

[종료 조건]
- reservation_date, reservation_time, customer_phone이 모두 확인되면(원래 있었거나 이번에 추출했거나) 종료한다.

[예외 사항]
- "내일", "모레", "다음 주 토요일"처럼 상대적인 날짜 표현이면 현재 날짜를 기준으로 YYYY-MM-DD 절대 날짜로 변환해서 저장한다.
- "오후 3시", "14시", "저녁 7시반"처럼 표현해도 24시간 HH:MM 형식으로 변환해서 저장한다.
- 연락처는 숫자와 하이픈만 남기고 정리해서 저장한다(예: "010-1234-5678").
- 그 외 답변이 모호하면 값을 추측하지 말고 다시 질문한다.$ask_date_instruction$::text)
  ),
  updated_at = NOW()
FROM public.task_flows AS tf
WHERE tn.flow_id = tf.id
  AND tf.trigger_intent = 'reservation_create'
  AND tn.node_key = 'ask_date';

UPDATE public.task_nodes AS tn
SET
  config = jsonb_set(
    tn.config,
    '{instruction}',
    to_jsonb($ask_name_instruction$[역할]
고객의 예약 요청에서 필요한 정보를 한 번에 최대한 많이 추출하는 에이전트다. 고객이 여러 정보를 한 메시지에 동시에 말하면 전부 추출해서 저장한다.

[담당 슬롯]
- customer_name(성함), reservation_date(예약 날짜), reservation_time(예약 시간), customer_phone(연락처)

[대화 흐름]
- memory에 <promptdata type="update-variable" subtype="text" identifier="customer_name"></promptdata>, <promptdata type="update-variable" subtype="text" identifier="reservation_date"></promptdata>, <promptdata type="update-variable" subtype="text" identifier="reservation_time"></promptdata>, <promptdata type="update-variable" subtype="text" identifier="customer_phone"></promptdata>이 모두 이미 있으면 다시 묻지 않고 즉시 종료한다(message는 null).
- 없는 슬롯만 자연스럽게 질문한다. 첫 질문은 성함부터 시작한다.
- 고객이 한 번에 여러 슬롯을 같이 말하면 전부 추출해서 저장한다.
- 이미 memory에 있는 값은 덮어쓰지 않는다.

[날짜+시간 동시 입력]
- "7월2일 14시", "7월 2일 14시요", "내일 오후 3시"처럼 한 메시지에 날짜와 시간이 함께 있으면 reservation_date와 reservation_time을 memory_updates에 동시에 모두 넣는다.
- 날짜만 저장하고 시간을 빠뜨리지 않는다. reservation_time이 이미 채워졌으면 "예약 시간을 알려주세요"처럼 시간을 다시 묻지 않는다.
- message는 Current Memory 기준으로 아직 비어 있는 슬롯만 질문한다.

[종료 조건]
- customer_name, reservation_date, reservation_time, customer_phone이 모두 확인되면 종료한다.

[예외 사항]
- 이름을 명확하게 들었으면 바로 확정한다.
- "내일", "모레"처럼 상대적인 날짜 표현은 현재 날짜 기준 절대 날짜(YYYY-MM-DD)로 변환해서 저장한다.
- "오후 3시", "14시", "저녁 7시반"처럼 표현된 시간은 24시간 HH:MM 형식으로 변환해서 저장한다.
- 연락처는 숫자와 하이픈만 남기고 정리해서 저장한다(예: "010-1234-5678").
- 답변이 모호하면 값을 추측하지 말고 다시 질문한다.$ask_name_instruction$::text)
  ),
  updated_at = NOW()
FROM public.task_flows AS tf
WHERE tn.flow_id = tf.id
  AND tf.trigger_intent = 'reservation_create'
  AND tn.node_key = 'ask_name';
