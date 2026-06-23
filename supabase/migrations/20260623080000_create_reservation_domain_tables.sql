create extension if not exists pgcrypto;

create table if not exists public.services (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null,
  description text,

  price integer,
  currency text default 'KRW',

  duration_minutes int not null,

  is_reservable boolean default true,
  is_active boolean default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  constraint services_duration_minutes_check check (duration_minutes > 0),
  constraint services_price_check check (price is null or price >= 0)
);

create table if not exists public.booking_calendars (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null,
  description text,

  calendar_type text default 'internal',
  external_provider text,
  external_calendar_id text,

  timezone text default 'Asia/Seoul',

  is_active boolean default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  constraint booking_calendars_calendar_type_check
    check (calendar_type in ('internal', 'google', 'outlook')),

  constraint booking_calendars_external_provider_check
    check (external_provider is null or external_provider in ('google', 'outlook'))
);

create table if not exists public.service_calendars (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  service_id uuid not null references public.services(id) on delete cascade,
  calendar_id uuid not null references public.booking_calendars(id) on delete cascade,

  is_default boolean default false,

  created_at timestamptz default now(),

  unique (service_id, calendar_id)
);

create table if not exists public.booking_availability_rules (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,
  calendar_id uuid not null references public.booking_calendars(id) on delete cascade,

  day_of_week int not null,
  start_time time not null,
  end_time time not null,

  is_active boolean default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  constraint booking_availability_rules_day_of_week_check
    check (day_of_week between 0 and 6),

  constraint booking_availability_rules_time_check
    check (start_time < end_time)
);

create table if not exists public.booking_policies (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,
  service_id uuid not null references public.services(id) on delete cascade,

  slot_interval_minutes int default 30,
  min_notice_minutes int default 60,
  max_days_ahead int default 30,

  requires_approval boolean default true,
  allow_customer_cancel boolean default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique (service_id),

  constraint booking_policies_slot_interval_check
    check (slot_interval_minutes > 0),

  constraint booking_policies_min_notice_check
    check (min_notice_minutes >= 0),

  constraint booking_policies_max_days_ahead_check
    check (max_days_ahead > 0)
);

create table if not exists public.customers (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text,
  phone text,
  email text,

  is_guest boolean default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.customers
add column if not exists name text;

alter table public.customers
add column if not exists phone text;

alter table public.customers
add column if not exists email text;

alter table public.customers
add column if not exists is_guest boolean default true;

alter table public.customers
add column if not exists updated_at timestamptz default now();

do $$
begin
  if exists (
    select 1
    from information_schema.tables
    where table_schema = 'public'
      and table_name = 'reservations'
  )
  and not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'reservations'
      and column_name = 'start_at'
  )
  then
    if not exists (
      select 1
      from information_schema.tables
      where table_schema = 'public'
        and table_name = 'reservations_backup_before_domain_v1'
    )
    then
      alter table public.reservations rename to reservations_backup_before_domain_v1;
    else
      alter table public.reservations rename to reservations_backup_before_domain_v2;
    end if;
  end if;
end $$;

create table if not exists public.reservations (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  conversation_id uuid references public.conversations(id) on delete set null,
  customer_id uuid references public.customers(id) on delete set null,

  service_id uuid references public.services(id) on delete set null,
  calendar_id uuid references public.booking_calendars(id) on delete set null,

  customer_name text,
  customer_phone text,
  customer_email text,

  start_at timestamptz not null,
  end_at timestamptz not null,
  timezone text default 'Asia/Seoul',

  status text default 'requested',

  source_channel text default 'web_chat',

  memo text,

  created_by text default 'ai',

  confirmed_at timestamptz,
  cancelled_at timestamptz,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  constraint reservations_time_check
    check (start_at < end_at),

  constraint reservations_status_check
    check (status in ('requested', 'confirmed', 'cancelled', 'rejected', 'completed', 'no_show')),

  constraint reservations_source_channel_check
    check (source_channel in ('web_chat', 'phone', 'kakao', 'admin')),

  constraint reservations_created_by_check
    check (created_by in ('ai', 'admin', 'customer'))
);

alter table public.reservations
add column if not exists customer_name text;

alter table public.reservations
add column if not exists customer_phone text;

alter table public.reservations
add column if not exists customer_email text;

alter table public.reservations
add column if not exists start_at timestamptz;

alter table public.reservations
add column if not exists end_at timestamptz;

alter table public.reservations
add column if not exists timezone text default 'Asia/Seoul';

alter table public.reservations
add column if not exists status text default 'requested';

alter table public.reservations
add column if not exists source_channel text default 'web_chat';

alter table public.reservations
add column if not exists memo text;

alter table public.reservations
add column if not exists created_by text default 'ai';

alter table public.reservations
add column if not exists confirmed_at timestamptz;

alter table public.reservations
add column if not exists cancelled_at timestamptz;

alter table public.reservations
add column if not exists updated_at timestamptz default now();

create index if not exists idx_services_organization_active
  on public.services (organization_id, is_active, is_reservable);

create index if not exists idx_booking_calendars_organization_active
  on public.booking_calendars (organization_id, is_active);

create index if not exists idx_service_calendars_service_id
  on public.service_calendars (service_id);

create index if not exists idx_service_calendars_calendar_id
  on public.service_calendars (calendar_id);

create index if not exists idx_booking_availability_rules_calendar_day
  on public.booking_availability_rules (calendar_id, day_of_week, is_active);

create index if not exists idx_booking_policies_service_id
  on public.booking_policies (service_id);

create index if not exists idx_customers_organization_phone
  on public.customers (organization_id, phone);

create index if not exists idx_reservations_organization_start_at
  on public.reservations (organization_id, start_at);

create index if not exists idx_reservations_calendar_time
  on public.reservations (calendar_id, start_at, end_at);

create index if not exists idx_reservations_status
  on public.reservations (organization_id, status);

create index if not exists idx_reservations_customer_phone
  on public.reservations (organization_id, customer_phone);

create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_services_updated_at on public.services;
create trigger set_services_updated_at
before update on public.services
for each row execute function public.set_updated_at();

drop trigger if exists set_booking_calendars_updated_at on public.booking_calendars;
create trigger set_booking_calendars_updated_at
before update on public.booking_calendars
for each row execute function public.set_updated_at();

drop trigger if exists set_booking_availability_rules_updated_at on public.booking_availability_rules;
create trigger set_booking_availability_rules_updated_at
before update on public.booking_availability_rules
for each row execute function public.set_updated_at();

drop trigger if exists set_booking_policies_updated_at on public.booking_policies;
create trigger set_booking_policies_updated_at
before update on public.booking_policies
for each row execute function public.set_updated_at();

drop trigger if exists set_customers_updated_at on public.customers;
create trigger set_customers_updated_at
before update on public.customers
for each row execute function public.set_updated_at();

drop trigger if exists set_reservations_updated_at on public.reservations;
create trigger set_reservations_updated_at
before update on public.reservations
for each row execute function public.set_updated_at();

notify pgrst, 'reload schema';