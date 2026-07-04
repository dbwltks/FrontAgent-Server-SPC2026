-- 예약 정보 수집을 ask_reservation_details 단일 instruction 노드로 통합한다.

INSERT INTO public.task_nodes (
  flow_id, node_key, node_type, label, config, position_x, position_y, timeout_seconds, retry_limit
)
SELECT
  tf.id,
  'ask_reservation_details',
  'instruction',
  '예약 정보 수집',
  $cfg${"instruction": "[역할]\n예약 접수에 필요한 고객 정보를 수집한다. Current Memory에 이미 있는 값은 절대 다시 묻지 않는다.\n\n[담당 슬롯]\n- customer_name (성함)\n- reservation_date (예약 날짜, YYYY-MM-DD)\n- reservation_time (예약 시간, HH:MM 24시간)\n- customer_phone (연락처)\n- party_size (인원수): <promptdata type=\"read-variable\" subtype=\"json\" identifier=\"service_item_resolve_result.service_item.raw_payload.requires_party_size\"></promptdata> 가 true일 때만 수집\n\n[대화 흐름]\n- memory에 <promptdata type=\"update-variable\" subtype=\"text\" identifier=\"customer_name\"></promptdata>, <promptdata type=\"update-variable\" subtype=\"text\" identifier=\"reservation_date\"></promptdata>, <promptdata type=\"update-variable\" subtype=\"text\" identifier=\"reservation_time\"></promptdata>, <promptdata type=\"update-variable\" subtype=\"text\" identifier=\"customer_phone\"></promptdata>, <promptdata type=\"update-variable\" subtype=\"text\" identifier=\"party_size\"></promptdata> 중 이번에 필요한 값이 모두 있으면 다시 묻지 않고 즉시 종료한다(message는 null).\n- 비어 있는 슬롯만 질문한다. 여러 슬롯이 비어 있으면 한 번에 같이 물어봐도 된다. 예: \"성함과 연락처, 원하시는 예약 날짜와 시간을 알려주세요.\"\n- 성함→날짜→시간→연락처처럼 한 항목씩만 순서대로 묻지 않는다. memory에 이미 있는 항목은 건너뛴다.\n- 고객이 한 메시지에 여러 정보를 말하면 전부 추출해서 저장한다.\n- 이미 memory에 있는 값은 덮어쓰지 않는다.\n\n[날짜+시간 동시 입력]\n- \"7월2일 14시\", \"7월 2일 14시요\", \"내일 오후 3시\"처럼 한 메시지에 날짜와 시간이 함께 있으면 reservation_date와 reservation_time을 memory_updates에 동시에 모두 넣는다.\n- 날짜만 저장하고 시간을 빠뜨리지 않는다. reservation_time이 이미 채워졌으면 시간을 다시 묻지 않는다.\n\n[종료 조건]\n- customer_name, reservation_date, reservation_time, customer_phone이 모두 확인되면 종료한다.\n- requires_party_size가 true이면 party_size도 확인된 뒤 종료한다.\n\n[예외 사항]\n- \"내일\", \"모레\"처럼 상대적인 날짜 표현은 현재 날짜 기준 YYYY-MM-DD로 변환해서 저장한다.\n- \"오후 3시\", \"14시\", \"저녁 7시반\"처럼 표현된 시간은 24시간 HH:MM 형식으로 변환해서 저장한다.\n- 연락처는 숫자와 하이픈만 남기고 정리해서 저장한다(예: \"010-1234-5678\").\n- requires_party_size가 false이면 party_size는 묻지 않는다.\n- 답변이 모호하면 값을 추측하지 말고 다시 질문한다.", "next_step_mode": "branch", "save_to_memory": true, "branch_node_key": "check_availability", "branch_condition": "memory.customer_name != null && memory.customer_name != \"\" && memory.reservation_date != null && memory.reservation_time != null && memory.customer_phone != null && (memory.service_item_resolve_result.service_item.raw_payload.requires_party_size != true || memory.party_size != null)", "fallback_node_key": "ask_reservation_details"}$cfg$::jsonb,
  1360,
  800,
  10,
  0
FROM public.task_flows tf
WHERE tf.trigger_intent = 'reservation_create'
ON CONFLICT (flow_id, node_key) DO UPDATE SET
  node_type = EXCLUDED.node_type,
  label = EXCLUDED.label,
  config = EXCLUDED.config,
  updated_at = NOW();

UPDATE public.task_nodes AS tn
SET config = jsonb_set(tn.config, '{next_node_key}', '"ask_reservation_details"'::jsonb),
    updated_at = NOW()
FROM public.task_flows tf
WHERE tn.flow_id = tf.id
  AND tf.trigger_intent = 'reservation_create'
  AND tn.node_key = 'resolve_service_item';

UPDATE public.task_edges te
SET target_node_key = 'ask_reservation_details', priority = 100
FROM public.task_flows tf
WHERE te.flow_id = tf.id
  AND tf.trigger_intent = 'reservation_create'
  AND te.source_node_key = 'resolve_service_item'
  AND te.target_node_key IN ('ask_party_size', 'ask_name');

INSERT INTO public.task_edges (
  flow_id, source_node_key, target_node_key, edge_type, condition_type, condition_config, is_failure_edge, priority
)
SELECT tf.id, 'ask_reservation_details', 'check_availability', 'single', 'always',
  jsonb_build_object('expression', $branch$memory.customer_name != null && memory.customer_name != "" && memory.reservation_date != null && memory.reservation_time != null && memory.customer_phone != null && (memory.service_item_resolve_result.service_item.raw_payload.requires_party_size != true || memory.party_size != null)$branch$), false, 100
FROM public.task_flows tf
WHERE tf.trigger_intent = 'reservation_create'
  AND NOT EXISTS (
    SELECT 1 FROM public.task_edges e
    WHERE e.flow_id = tf.id
      AND e.source_node_key = 'ask_reservation_details'
      AND e.target_node_key = 'check_availability'
      AND e.edge_type = 'single'
  );

INSERT INTO public.task_edges (
  flow_id, source_node_key, target_node_key, edge_type, condition_type, condition_config, is_failure_edge, priority
)
SELECT tf.id, 'ask_reservation_details', 'ask_reservation_details', 'fallback', 'fallback',
  jsonb_build_object('fallback', true, 'expression', $branch$memory.customer_name != null && memory.customer_name != "" && memory.reservation_date != null && memory.reservation_time != null && memory.customer_phone != null && (memory.service_item_resolve_result.service_item.raw_payload.requires_party_size != true || memory.party_size != null)$branch$), true, 200
FROM public.task_flows tf
WHERE tf.trigger_intent = 'reservation_create'
  AND NOT EXISTS (
    SELECT 1 FROM public.task_edges e
    WHERE e.flow_id = tf.id
      AND e.source_node_key = 'ask_reservation_details'
      AND e.target_node_key = 'ask_reservation_details'
      AND e.edge_type = 'fallback'
  );
