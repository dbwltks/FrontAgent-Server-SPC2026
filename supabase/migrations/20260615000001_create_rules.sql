create table public.rules (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,                    -- 어느 회사의 룰인지
  name text not null,                               -- 룰 이름
  description text,                                 -- 룰 설명
  rule_type text not null default 'general',        -- 룰 분류
  trigger_condition text,                           -- 언제 이 룰을 적용할지
  instruction text not null,                        -- AI에게 줄 지시문
  filters jsonb default '[]'::jsonb,                -- 룰을 감지할 키워드 목록

  action_type text not null default 'warn',         -- 룰 적용 방식: block, warn, handoff
  response_message text,                            -- 룰에 걸렸을 때 사용자에게 보여줄 문구

  priority integer default 0,                       -- 우선순위
  is_active boolean default true,                   -- 활성화 여부
  created_at timestamp without time zone default now(),
  updated_at timestamp without time zone default now()
);