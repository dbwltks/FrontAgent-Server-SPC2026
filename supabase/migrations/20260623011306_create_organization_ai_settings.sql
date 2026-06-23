create table public.organization_ai_settings (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null unique references public.organizations(id) on delete cascade,
  llm_provider text not null default 'openai',
  llm_model text not null default 'gpt-4.1-mini',
  decision_model text,
  voice_enabled boolean not null default true,
  voice_mode text not null default 'pipeline'
    check (voice_mode in ('pipeline', 'realtime')),
  voice_stt_model text not null default 'gpt-4o-mini-transcribe',
  voice_tts_model text not null default 'gpt-4o-mini-tts',
  voice_tts_voice text not null default 'marin',
  realtime_model text not null default 'gpt-realtime-2',
  realtime_voice text not null default 'marin',
  voice_response_style text not null default 'friendly_short'
    check (voice_response_style in ('friendly_short', 'professional_short', 'casual_short')),
  monthly_budget_limit_cents integer,
  monthly_token_limit integer,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

insert into public.organization_ai_settings (
  organization_id,
  llm_provider,
  llm_model
)
select
  id,
  llm_provider,
  llm_model
from public.organizations
on conflict (organization_id) do nothing;

create table public.ai_usage_logs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  conversation_id uuid,
  session_id text,
  channel text,
  feature text not null check (feature in ('chat', 'stt', 'tts', 'realtime')),
  provider text not null default 'openai',
  model text not null,
  input_tokens integer,
  output_tokens integer,
  total_tokens integer,
  audio_duration_ms integer,
  audio_bytes integer,
  text_chars integer,
  estimated_cost_cents integer,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz default now()
);

create index organization_ai_settings_organization_id_idx
  on public.organization_ai_settings (organization_id);

create index ai_usage_logs_organization_created_at_idx
  on public.ai_usage_logs (organization_id, created_at desc);

create index ai_usage_logs_organization_feature_created_at_idx
  on public.ai_usage_logs (organization_id, feature, created_at desc);

alter table public.organization_ai_settings enable row level security;
alter table public.ai_usage_logs enable row level security;
