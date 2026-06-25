-- Convert legacy ask nodes into instruction nodes.
-- The conversational <promptdata> tag replaces variable_name/question fields.

UPDATE public.task_nodes
SET
  node_type = 'instruction',
  config = jsonb_build_object(
    'instruction',
    '[역할]' || E'\n' ||
    '고객에게 필요한 정보를 자연스럽게 질문하고 답변에서 값을 추출하는 에이전트다.' || E'\n\n' ||
    '[대화 흐름]' || E'\n' ||
    '- ' || COALESCE(config->>'question', label, '필요한 정보를 입력해 주세요.') || E'\n\n' ||
    '[종료 조건]' || E'\n' ||
    '- ' || COALESCE(config->>'variable_name', 'answer') || ' 값을 확인하면 종료한다.' || E'\n\n' ||
    '[예외 사항]' || E'\n' ||
    '- 답변이 모호하면 값을 추측하지 말고 다시 질문한다.' || E'\n\n' ||
    '<promptdata type="update-variable" subtype="text" identifier="' || COALESCE(config->>'variable_name', 'answer') || '"></promptdata>',
    'save_to_memory', true,
    'next_step_mode', COALESCE(config->>'next_step_mode', 'single')
  )
    || (
      CASE WHEN config ? 'next_node_key'
        THEN jsonb_build_object('next_node_key', config->>'next_node_key')
        ELSE '{}'::jsonb
      END
    )
    || (
      CASE WHEN config ? 'branch_condition'
        THEN jsonb_build_object('branch_condition', config->>'branch_condition')
        ELSE '{}'::jsonb
      END
    )
    || (
      CASE WHEN config ? 'branch_node_key'
        THEN jsonb_build_object('branch_node_key', config->>'branch_node_key')
        ELSE '{}'::jsonb
      END
    )
    || (
      CASE WHEN config ? 'fallback_node_key'
        THEN jsonb_build_object('fallback_node_key', config->>'fallback_node_key')
        ELSE '{}'::jsonb
      END
    ),
  updated_at = NOW()
WHERE node_type = 'ask';

ALTER TABLE public.task_nodes
  DROP CONSTRAINT IF EXISTS task_nodes_node_type_check;

ALTER TABLE public.task_nodes
  ADD CONSTRAINT task_nodes_node_type_check CHECK (
    node_type IN (
      'message',
      'instruction',
      'function',
      'condition',
      'human_approval',
      'handoff',
      'code'
    )
  );
