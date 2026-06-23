with target_org as (
  select id as organization_id
  from public.organizations
  order by created_at
  limit 1
),

insert_services as (
  insert into public.services (
    organization_id,
    name,
    description,
    price,
    currency,
    duration_minutes,
    is_reservable,
    is_active
  )
  select
    target_org.organization_id,
    service_data.name,
    service_data.description,
    service_data.price,
    'KRW',
    service_data.duration_minutes,
    true,
    true
  from target_org
  cross join (
    values
      ('기본 청소', '기본적인 생활 청소 서비스입니다.', 30000, 60),
      ('프리미엄 청소', '꼼꼼한 프리미엄 청소 서비스입니다.', 50000, 90),
      ('입주 청소', '입주 전 전체 공간을 청소하는 서비스입니다.', 120000, 180)
  ) as service_data(name, description, price, duration_minutes)
  where not exists (
    select 1
    from public.services s
    where s.organization_id = target_org.organization_id
      and s.name = service_data.name
  )
  returning id, organization_id, name
),

insert_calendar as (
  insert into public.booking_calendars (
    organization_id,
    name,
    description,
    calendar_type,
    timezone,
    is_active
  )
  select
    target_org.organization_id,
    '한빛클리닝 대표 캘린더',
    '데모 예약을 받는 기본 캘린더입니다.',
    'internal',
    'Asia/Seoul',
    true
  from target_org
  where not exists (
    select 1
    from public.booking_calendars bc
    where bc.organization_id = target_org.organization_id
      and bc.name = '한빛클리닝 대표 캘린더'
  )
  returning id, organization_id
),

calendar_ref as (
  select id, organization_id
  from insert_calendar

  union all

  select bc.id, bc.organization_id
  from public.booking_calendars bc
  join target_org
    on target_org.organization_id = bc.organization_id
  where bc.name = '한빛클리닝 대표 캘린더'
  order by id
  limit 1
),

service_ref as (
  select
    s.id,
    s.organization_id,
    s.name
  from public.services s
  join target_org
    on target_org.organization_id = s.organization_id
  where s.name in ('기본 청소', '프리미엄 청소', '입주 청소')
    and s.is_active = true
    and s.is_reservable = true
),

insert_service_calendars as (
  insert into public.service_calendars (
    organization_id,
    service_id,
    calendar_id,
    is_default
  )
  select
    service_ref.organization_id,
    service_ref.id,
    calendar_ref.id,
    true
  from service_ref
  cross join calendar_ref
  on conflict (service_id, calendar_id) do nothing
  returning id
),

insert_policies as (
  insert into public.booking_policies (
    organization_id,
    service_id,
    slot_interval_minutes,
    min_notice_minutes,
    max_days_ahead,
    requires_approval,
    allow_customer_cancel
  )
  select
    service_ref.organization_id,
    service_ref.id,
    30,
    60,
    30,
    true,
    true
  from service_ref
  on conflict (service_id) do nothing
  returning id
)

insert into public.booking_availability_rules (
  organization_id,
  calendar_id,
  day_of_week,
  start_time,
  end_time,
  is_active
)
select
  calendar_ref.organization_id,
  calendar_ref.id,
  rule_data.day_of_week,
  rule_data.start_time::time,
  rule_data.end_time::time,
  true
from calendar_ref
cross join (
  values
    (1, '10:00', '18:00'),
    (2, '10:00', '18:00'),
    (3, '10:00', '18:00'),
    (4, '10:00', '18:00'),
    (5, '10:00', '18:00'),
    (6, '10:00', '14:00')
) as rule_data(day_of_week, start_time, end_time)
where not exists (
  select 1
  from public.booking_availability_rules bar
  where bar.organization_id = calendar_ref.organization_id
    and bar.calendar_id = calendar_ref.id
    and bar.day_of_week = rule_data.day_of_week
    and bar.start_time = rule_data.start_time::time
    and bar.end_time = rule_data.end_time::time
);

notify pgrst, 'reload schema';