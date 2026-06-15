create table public.conversations (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,
  session_id text not null,
  channel text not null default 'web_chat',
  customer_id uuid,
  customer_name text,
  customer_email text,
  customer_phone text,
  status text not null default 'open',
  assigned_admin_id uuid,
  last_message text,
  last_message_at timestamp without time zone default now(),
  created_at timestamp without time zone default now(),
  updated_at timestamp without time zone default now(),
  ai_enabled boolean default true
);

alter table public.conversations enable row level security;
