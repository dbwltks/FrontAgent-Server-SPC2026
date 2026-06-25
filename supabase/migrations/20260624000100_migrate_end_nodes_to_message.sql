-- Convert legacy end nodes into terminal message nodes.
-- The node row is kept so existing incoming edges remain valid.

UPDATE public.task_nodes
SET
  node_type = 'message',
  label = CASE
    WHEN label IS NULL OR btrim(label) = '' OR lower(label) = 'end' THEN '완료 메시지'
    ELSE label
  END,
  config = jsonb_set(
    jsonb_set(
      CASE
        WHEN COALESCE(config, '{}'::jsonb) ? 'message' THEN COALESCE(config, '{}'::jsonb)
        ELSE COALESCE(config, '{}'::jsonb) || jsonb_build_object('message', '태스크를 종료합니다.')
      END,
      '{next_step_mode}',
      to_jsonb('end'::text),
      true
    ),
    '{status}',
    'null'::jsonb,
    true
  ) - 'status',
  updated_at = NOW()
WHERE node_type = 'end';

ALTER TABLE public.task_nodes
  DROP CONSTRAINT IF EXISTS task_nodes_node_type_check;

ALTER TABLE public.task_nodes
  ADD CONSTRAINT task_nodes_node_type_check CHECK (
    node_type IN (
      'message',
      'ask',
      'instruction',
      'function',
      'condition',
      'human_approval',
      'handoff',
      'code'
    )
  );
