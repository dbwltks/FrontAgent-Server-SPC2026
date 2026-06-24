create extension if not exists "pgcrypto";

create table if not exists public.calendar_integrations (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null
    references public.organizations(id)
    on delete cascade,

  provider text not null default 'google',

  -- Google 계정 정보
  account_email text,
  account_name text,

  -- 연결할 외부 캘린더 ID
  -- 기본 캘린더는 Google에서 primary 사용
  external_calendar_id text not null default 'primary',

  -- OAuth 정보
  access_token text,
  refresh_token text,
  token_type text default 'Bearer',
  expires_at timestamptz,

  -- 권한 범위
  scopes text[] not null default array[
    'https://www.googleapis.com/auth/calendar.events'
  ],

  status text not null default 'pending',

  last_error text,

  connected_at timestamptz,
  disconnected_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint calendar_integrations_provider_check
    check (provider in ('google', 'outlook')),

  constraint calendar_integrations_status_check
    check (status in ('pending', 'connected', 'disconnected', 'failed'))
);

create unique index if not exists uq_calendar_integrations_org_provider_calendar
  on public.calendar_integrations (
    organization_id,
    provider,
    external_calendar_id
  );

create index if not exists idx_calendar_integrations_organization_id
  on public.calendar_integrations (organization_id);

create index if not exists idx_calendar_integrations_provider
  on public.calendar_integrations (provider);

drop trigger if exists trg_calendar_integrations_updated_at
on public.calendar_integrations;

create trigger trg_calendar_integrations_updated_at
before update on public.calendar_integrations
for each row
execute function public.set_updated_at();

notify pgrst, 'reload schema';