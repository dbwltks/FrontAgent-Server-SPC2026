create table public.rules (
  id uuid primary key default gen_random_uuid(),

  organization_id text not null,

  name text not null,

  instruction text not null,

  is_active boolean default true,

  created_at timestamp without time zone default now(),

  updated_at timestamp without time zone default now()
);
