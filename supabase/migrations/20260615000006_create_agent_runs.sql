create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,
  session_id text not null,
  user_message text not null,
  intent text,
  applied_rules jsonb default '[]'::jsonb,
  used_knowledge jsonb default '[]'::jsonb,
  final_response text,
  status text not null default 'success',
  error_message text,
  created_at timestamp without time zone default now()
);

alter table public.agent_runs enable row level security;
