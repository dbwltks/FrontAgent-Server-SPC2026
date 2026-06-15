create table public.knowledge_sources (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,
  folder_id uuid,
  title text not null,
  source_type text not null default 'text',
  file_url text,
  file_name text,
  mime_type text,
  status text not null default 'indexed',
  is_referenced boolean default true,
  resolution_rate numeric default 0,
  reference_count integer default 0,
  created_at timestamp without time zone default now(),
  updated_at timestamp without time zone default now()
);
