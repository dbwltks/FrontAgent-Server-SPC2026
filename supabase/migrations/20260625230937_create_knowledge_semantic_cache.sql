create table public.knowledge_semantic_cache (
  id uuid primary key default gen_random_uuid(),
  organization_id text not null,
  folder_id uuid,
  query_embedding vector not null,
  result jsonb not null,
  created_at timestamp without time zone default now()
);

create index knowledge_semantic_cache_org_idx
  on public.knowledge_semantic_cache (organization_id);

-- 캐시 엔트리는 지식이 갱신되어도 자연히 오래된 답을 줄 수 있으므로
-- 짧은 주기로 직접 정리한다(앱에서 주기적으로 delete 호출, 또는 pg_cron).
create index knowledge_semantic_cache_created_at_idx
  on public.knowledge_semantic_cache (created_at);

create or replace function match_knowledge_semantic_cache(
  query_embedding vector,
  match_organization_id text,
  match_folder_id uuid default null,
  match_threshold float default 0.93
)
returns table (
  result jsonb,
  similarity float
)
language sql
as $$
  select
    ksc.result,
    1 - (ksc.query_embedding <=> query_embedding) as similarity
  from public.knowledge_semantic_cache ksc
  where ksc.organization_id = match_organization_id
    and (
      match_folder_id is null
      or ksc.folder_id = match_folder_id
    )
  order by ksc.query_embedding <=> query_embedding
  limit 1;
$$;
