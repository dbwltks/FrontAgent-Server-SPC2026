create extension if not exists "pgcrypto";

create table if not exists public.reservation_calendar_events (
  id uuid primary key default gen_random_uuid(),

  organization_id text not null,

  reservation_id uuid not null
    references public.reservations(id)
    on delete cascade,

  calendar_id uuid
    references public.booking_calendars(id)
    on delete set null,

  provider text not null default 'google',
  -- google, outlook, internal

  external_event_id text,
  external_event_url text,

  sync_status text not null default 'pending',
  -- pending, synced, failed, cancelled

  error_message text,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  constraint reservation_calendar_events_provider_check
    check (provider in ('google', 'outlook', 'internal')),

  constraint reservation_calendar_events_sync_status_check
    check (sync_status in ('pending', 'synced', 'failed', 'cancelled'))
);

create index if not exists idx_reservation_calendar_events_organization_id
  on public.reservation_calendar_events (organization_id);

create index if not exists idx_reservation_calendar_events_reservation_id
  on public.reservation_calendar_events (reservation_id);

create index if not exists idx_reservation_calendar_events_calendar_id
  on public.reservation_calendar_events (calendar_id);

create index if not exists idx_reservation_calendar_events_provider_event_id
  on public.reservation_calendar_events (provider, external_event_id);

create unique index if not exists uq_reservation_calendar_events_provider_event
  on public.reservation_calendar_events (provider, external_event_id)
  where external_event_id is not null;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_reservation_calendar_events_updated_at
on public.reservation_calendar_events;

create trigger trg_reservation_calendar_events_updated_at
before update on public.reservation_calendar_events
for each row
execute function public.set_updated_at();

notify pgrst, 'reload schema';