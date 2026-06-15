create extension if not exists "vector";

create table public.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,
  source_id uuid references public.knowledge_sources(id),
  folder_id uuid,
  chunk_index integer not null,
  content text not null,
  embedding vector,
  metadata jsonb default '{}'::jsonb,
  created_at timestamp without time zone default now()
);
