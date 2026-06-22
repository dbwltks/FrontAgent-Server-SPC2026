create extension if not exists pgcrypto;

create table if not exists public.task_flows (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null,
  description text,

  trigger_intent text,
  trigger_description text,
  trigger_examples jsonb default '[]'::jsonb,

  allowed_channels jsonb default '["chat", "voice"]'::jsonb,
  filters jsonb default '{}'::jsonb,

  is_enabled boolean default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.task_nodes (
  id uuid primary key default gen_random_uuid(),
  flow_id uuid not null references public.task_flows(id) on delete cascade,

  node_key text not null,
  node_type text not null,
  label text not null,

  config jsonb default '{}'::jsonb,
  code text,

  position_x int default 0,
  position_y int default 0,

  timeout_seconds int default 10,
  retry_limit int default 0,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique(flow_id, node_key),

  constraint task_nodes_node_type_check check (
    node_type in (
      'message',
      'ask',
      'instruction',
      'function',
      'condition',
      'human_approval',
      'handoff',
      'end',
      'code'
    )
  )
);

create table if not exists public.task_edges (
  id uuid primary key default gen_random_uuid(),
  flow_id uuid not null references public.task_flows(id) on delete cascade,

  source_node_key text not null,
  target_node_key text not null,

  edge_type text default 'single',
  condition_type text default 'always',
  condition_config jsonb default '{}'::jsonb,

  is_failure_edge boolean default false,
  priority int default 100,

  created_at timestamptz default now(),

  constraint task_edges_edge_type_check check (
    edge_type in ('single', 'condition', 'failure')
  )
);

create table if not exists public.task_sessions (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,
  session_id text not null,
  flow_id uuid not null references public.task_flows(id) on delete cascade,

  current_node_key text not null,
  waiting_node_key text,

  variables jsonb default '{}'::jsonb,

  status text default 'running',
  approval_status text,
  last_error jsonb,
  expires_at timestamptz,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique(organization_id, session_id, flow_id),

  constraint task_sessions_status_check check (
    status in (
      'running',
      'waiting_user_input',
      'approval_waiting',
      'completed',
      'cancelled',
      'handoff',
      'failed',
      'expired'
    )
  )
);

create index if not exists idx_task_flows_organization_id
on public.task_flows(organization_id);

create index if not exists idx_task_flows_enabled
on public.task_flows(organization_id, is_enabled);

create index if not exists idx_task_nodes_flow_id
on public.task_nodes(flow_id);

create index if not exists idx_task_edges_flow_id
on public.task_edges(flow_id);

create index if not exists idx_task_edges_source_node
on public.task_edges(flow_id, source_node_key);

create index if not exists idx_task_sessions_lookup
on public.task_sessions(organization_id, session_id, status);

create index if not exists idx_task_sessions_expires_at
on public.task_sessions(expires_at);

create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_task_flows_updated_at on public.task_flows;
create trigger set_task_flows_updated_at
before update on public.task_flows
for each row
execute function public.set_updated_at();

drop trigger if exists set_task_nodes_updated_at on public.task_nodes;
create trigger set_task_nodes_updated_at
before update on public.task_nodes
for each row
execute function public.set_updated_at();

drop trigger if exists set_task_sessions_updated_at on public.task_sessions;
create trigger set_task_sessions_updated_at
before update on public.task_sessions
for each row
execute function public.set_updated_at();