create table public.conversation_messages (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,
  conversation_id uuid not null references public.conversations(id),
  sender_type text not null,
  sender_name text,
  message text not null,
  metadata jsonb default '{}'::jsonb,
  created_at timestamp without time zone default now()
);

alter table public.conversation_messages enable row level security;
