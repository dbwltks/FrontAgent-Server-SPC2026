create table if not exists public.booking_settings (
  id uuid primary key default gen_random_uuid(),

  organization_id uuid not null references public.organizations(id) on delete cascade,

  name text not null default '대표 예약 캘린더',
  timezone text not null default 'Asia/Seoul',

  -- 예약 정책
  slot_interval_minutes int not null default 30,
  min_notice_minutes int not null default 60,
  max_days_ahead int not null default 30,
  requires_approval boolean not null default true,
  allow_customer_cancel boolean not null default true,

  -- 반복 운영시간
  weekly_hours jsonb not null default '[]'::jsonb,

  -- 휴무일 / 임시 운영시간 / 특정일 예외
  exceptions jsonb not null default '[]'::jsonb,

  -- 기존 booking_policies가 서비스별로 여러 개 있을 경우 데이터 보존용
  -- MVP에서는 당장 사용하지 않아도 됨
  service_policy_overrides jsonb not null default '{}'::jsonb,

  -- 기존 booking_calendars id 보존용
  -- 나중에 데이터 확인 또는 롤백할 때 사용
  legacy_calendar_ids jsonb not null default '[]'::jsonb,

  is_active boolean not null default true,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique (organization_id),

  constraint booking_settings_slot_interval_check check (slot_interval_minutes > 0),
  constraint booking_settings_min_notice_check check (min_notice_minutes >= 0),
  constraint booking_settings_max_days_ahead_check check (max_days_ahead > 0)
);

create index if not exists idx_booking_settings_organization_id
on public.booking_settings (organization_id);

drop trigger if exists set_booking_settings_updated_at on public.booking_settings;

create trigger set_booking_settings_updated_at
before update on public.booking_settings
for each row
execute function public.set_updated_at();


-- 기존 예약 도메인 테이블 데이터를 booking_settings로 이관한다.
-- 조직당 booking_settings 1개를 만든다.
insert into public.booking_settings (
  organization_id,
  name,
  timezone,
  slot_interval_minutes,
  min_notice_minutes,
  max_days_ahead,
  requires_approval,
  allow_customer_cancel,
  weekly_hours,
  exceptions,
  service_policy_overrides,
  legacy_calendar_ids,
  is_active
)
select
  orgs.organization_id,

  coalesce(first_calendar.name, '대표 예약 캘린더') as name,
  coalesce(first_calendar.timezone, 'Asia/Seoul') as timezone,

  coalesce(first_policy.slot_interval_minutes, 30) as slot_interval_minutes,
  coalesce(first_policy.min_notice_minutes, 60) as min_notice_minutes,
  coalesce(first_policy.max_days_ahead, 30) as max_days_ahead,
  coalesce(first_policy.requires_approval, true) as requires_approval,
  coalesce(first_policy.allow_customer_cancel, true) as allow_customer_cancel,

  coalesce(weekly_hours.weekly_hours, '[]'::jsonb) as weekly_hours,

  '[]'::jsonb as exceptions,

  coalesce(policy_overrides.service_policy_overrides, '{}'::jsonb) as service_policy_overrides,
  coalesce(legacy_calendars.legacy_calendar_ids, '[]'::jsonb) as legacy_calendar_ids,

  true as is_active

from (
  select organization_id from public.services
  union
  select organization_id from public.booking_calendars
  union
  select organization_id from public.booking_policies
  union
  select organization_id from public.reservations
) orgs

left join lateral (
  select
    c.id,
    c.name,
    c.timezone
  from public.booking_calendars c
  where c.organization_id = orgs.organization_id
  order by c.created_at asc
  limit 1
) first_calendar on true

left join lateral (
  select
    p.slot_interval_minutes,
    p.min_notice_minutes,
    p.max_days_ahead,
    p.requires_approval,
    p.allow_customer_cancel
  from public.booking_policies p
  where p.organization_id = orgs.organization_id
  order by p.created_at asc
  limit 1
) first_policy on true

left join lateral (
  select
    jsonb_agg(
      jsonb_build_object(
        'day_of_week', r.day_of_week,
        'start_time', to_char(r.start_time, 'HH24:MI'),
        'end_time', to_char(r.end_time, 'HH24:MI'),
        'is_active', r.is_active
      )
      order by r.day_of_week, r.start_time
    ) as weekly_hours
  from public.booking_availability_rules r
  where r.organization_id = orgs.organization_id
    and r.is_active = true
) weekly_hours on true

left join lateral (
  select
    jsonb_object_agg(
      p.service_id::text,
      jsonb_build_object(
        'slot_interval_minutes', p.slot_interval_minutes,
        'min_notice_minutes', p.min_notice_minutes,
        'max_days_ahead', p.max_days_ahead,
        'requires_approval', p.requires_approval,
        'allow_customer_cancel', p.allow_customer_cancel
      )
    ) as service_policy_overrides
  from public.booking_policies p
  where p.organization_id = orgs.organization_id
) policy_overrides on true

left join lateral (
  select
    jsonb_agg(
      jsonb_build_object(
        'id', c.id,
        'name', c.name,
        'calendar_type', c.calendar_type,
        'external_provider', c.external_provider,
        'external_calendar_id', c.external_calendar_id,
        'timezone', c.timezone,
        'is_active', c.is_active
      )
      order by c.created_at asc
    ) as legacy_calendar_ids
  from public.booking_calendars c
  where c.organization_id = orgs.organization_id
) legacy_calendars on true

on conflict (organization_id)
do update set
  name = excluded.name,
  timezone = excluded.timezone,
  slot_interval_minutes = excluded.slot_interval_minutes,
  min_notice_minutes = excluded.min_notice_minutes,
  max_days_ahead = excluded.max_days_ahead,
  requires_approval = excluded.requires_approval,
  allow_customer_cancel = excluded.allow_customer_cancel,
  weekly_hours = excluded.weekly_hours,
  service_policy_overrides = excluded.service_policy_overrides,
  legacy_calendar_ids = excluded.legacy_calendar_ids,
  updated_at = now();


-- booking_availability_exceptions 테이블이 있는 경우 exceptions로 이관한다.
do $$
begin
  if exists (
    select 1
    from information_schema.tables
    where table_schema = 'public'
      and table_name = 'booking_availability_exceptions'
  ) then
    execute $sql$
      update public.booking_settings bs
      set
        exceptions = coalesce(ex.exception_list, '[]'::jsonb),
        updated_at = now()
      from (
        select
          organization_id,
          jsonb_agg(
            jsonb_build_object(
              'date', exception_date,
              'is_closed', is_closed,
              'start_time', case
                when start_time is null then null
                else to_char(start_time, 'HH24:MI')
              end,
              'end_time', case
                when end_time is null then null
                else to_char(end_time, 'HH24:MI')
              end,
              'reason', reason
            )
            order by exception_date asc
          ) as exception_list
        from public.booking_availability_exceptions
        group by organization_id
      ) ex
      where bs.organization_id = ex.organization_id
    $sql$;
  end if;
end $$;


-- 기존 테이블은 아직 삭제하지 않는다.
-- 현재 서버 코드가 아직 이 테이블들을 참조하고 있기 때문이다.
-- 대신 사용 중단 예정 표시만 남긴다.

comment on table public.booking_calendars is
'DEPRECATED: MVP 단순화 이후 booking_settings로 통합 예정. 코드 수정 완료 전까지 삭제 금지.';

comment on table public.service_calendars is
'DEPRECATED: MVP 단순화 이후 booking_settings 구조에서는 사용하지 않음. 코드 수정 완료 전까지 삭제 금지.';

comment on table public.booking_availability_rules is
'DEPRECATED: MVP 단순화 이후 booking_settings.weekly_hours로 통합 예정. 코드 수정 완료 전까지 삭제 금지.';

comment on table public.booking_policies is
'DEPRECATED: MVP 단순화 이후 booking_settings 정책 컬럼으로 통합 예정. 코드 수정 완료 전까지 삭제 금지.';


do $$
begin
  if exists (
    select 1
    from information_schema.tables
    where table_schema = 'public'
      and table_name = 'booking_availability_exceptions'
  ) then
    execute $sql$
      comment on table public.booking_availability_exceptions is
      'DEPRECATED: MVP 단순화 이후 booking_settings.exceptions로 통합 예정. 코드 수정 완료 전까지 삭제 금지.'
    $sql$;
  end if;
end $$;


notify pgrst, 'reload schema';