create or replace function match_knowledge_chunks(
  query_embedding vector,
  match_organization_id text,
  match_count integer,
  match_folder_id uuid default null
)
returns table (
  id uuid,
  source_id uuid,
  source_title text,
  folder_id uuid,
  content text,
  metadata jsonb,
  similarity float
)
language sql
as $$
  select
    kc.id,
    kc.source_id,
    ks.title as source_title,
    kc.folder_id,
    kc.content,
    kc.metadata,
    1 - (kc.embedding <=> query_embedding) as similarity
  from public.knowledge_chunks kc
  join public.knowledge_sources ks
    on ks.id = kc.source_id
  where kc.organization_id = match_organization_id
    and kc.embedding is not null
    and ks.status = 'indexed'
    and ks.is_referenced = true
    and (
      match_folder_id is null
      or kc.folder_id = match_folder_id
    )
  order by kc.embedding <=> query_embedding
  limit match_count;
$$;
