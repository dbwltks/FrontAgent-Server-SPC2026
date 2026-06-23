-- 예약/상품/고객 목업 데이터를 저장하기 위한 운영 테이블 생성
-- 목적:
-- 1. "예약 취소하고 싶어요" 같은 태스크가 실제 DB 데이터를 조회/변경할 수 있게 한다.
-- 2. Function Node에서 예약 조회, 예약 취소 함수를 만들 때 사용할 기본 도메인 테이블을 준비한다.

create table if not exists public.customers (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null,
  phone text,
  email text,

  memo text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null,
  description text,
  price integer not null default 0,
  duration_minutes integer not null default 60,

  is_active boolean not null default true,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.reservations (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  customer_id uuid references public.customers(id) on delete set null,
  product_id uuid references public.products(id) on delete set null,

  -- 테스트와 실제 응답에서 고객 정보를 쉽게 보여주기 위해 예약 시점의 값을 중복 저장한다.
  customer_name text not null,
  customer_phone text,

  -- 상품명이 나중에 바뀌어도 예약 당시 상품명을 보존하기 위해 중복 저장한다.
  product_name text not null,

  scheduled_start_at timestamptz not null,
  scheduled_end_at timestamptz,

  status text not null default 'reserved',

  cancel_reason text,
  cancelled_at timestamptz,

  -- Google Calendar 연동용.
  -- 지금 당장은 null로 두고, 나중에 캘린더 이벤트 생성 후 event_id를 저장한다.
  google_calendar_event_id text,
  calendar_sync_status text,
  calendar_sync_error jsonb,

  metadata jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint reservations_status_check check (
    status in (
      'reserved',
      'confirmed',
      'cancelled',
      'completed',
      'no_show'
    )
  )
);

create index if not exists idx_customers_organization_id
  on public.customers (organization_id);

create index if not exists idx_customers_name
  on public.customers (name);

create index if not exists idx_customers_phone
  on public.customers (phone);

create index if not exists idx_products_organization_id
  on public.products (organization_id);

create index if not exists idx_products_name
  on public.products (name);

create index if not exists idx_reservations_organization_id
  on public.reservations (organization_id);

create index if not exists idx_reservations_customer_name
  on public.reservations (customer_name);

create index if not exists idx_reservations_customer_phone
  on public.reservations (customer_phone);

create index if not exists idx_reservations_status
  on public.reservations (status);

create index if not exists idx_reservations_scheduled_start_at
  on public.reservations (scheduled_start_at);