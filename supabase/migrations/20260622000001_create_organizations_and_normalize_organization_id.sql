-- organizations 테이블 생성 + 기존 모든 테이블의 organization_id(text)를
-- organizations.id(uuid)로 정규화한다.
--
-- 지금까지 모든 데이터가 단일 테스트 조직("org_test", 일부는 제어문자가 섞인
-- "\borg_test")으로 들어가 있어 다중 조직 마이그레이션 전략 없이
-- 모든 행을 새로 만든 기본 조직 UUID로 통일한다.

-- 1. organizations 테이블
create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  llm_provider text not null default 'openai',
  llm_model text not null default 'gpt-4.1-mini',
  streaming_model text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- 2. 기본 조직 1행 생성
insert into organizations (id, name, llm_provider, llm_model)
values (gen_random_uuid(), '기본 조직', 'openai', 'gpt-4.1-mini');

-- 3. 각 테이블의 organization_id 값을 전부 기본 조직 UUID로 덮어써서
--    제어문자가 섞인 손상값까지 한 번에 정리한 뒤, 컬럼 타입을
--    text -> uuid로 변경하고 FK를 추가한다.
do $$
declare
  default_org_id uuid;
begin
  select id into default_org_id from organizations limit 1;

  -- rules
  update rules set organization_id = default_org_id::text;
  alter table rules alter column organization_id type uuid using organization_id::uuid;
  alter table rules add constraint rules_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;

  -- knowledge_sources
  update knowledge_sources set organization_id = default_org_id::text;
  alter table knowledge_sources alter column organization_id type uuid using organization_id::uuid;
  alter table knowledge_sources add constraint knowledge_sources_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;

  -- knowledge_chunks
  update knowledge_chunks set organization_id = default_org_id::text;
  alter table knowledge_chunks alter column organization_id type uuid using organization_id::uuid;
  alter table knowledge_chunks add constraint knowledge_chunks_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;

  -- conversations
  update conversations set organization_id = default_org_id::text;
  alter table conversations alter column organization_id type uuid using organization_id::uuid;
  alter table conversations add constraint conversations_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;

  -- conversation_messages
  update conversation_messages set organization_id = default_org_id::text;
  alter table conversation_messages alter column organization_id type uuid using organization_id::uuid;
  alter table conversation_messages add constraint conversation_messages_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;

  -- agent_runs
  update agent_runs set organization_id = default_org_id::text;
  alter table agent_runs alter column organization_id type uuid using organization_id::uuid;
  alter table agent_runs add constraint agent_runs_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;

  -- knowledge_folders
  update knowledge_folders set organization_id = default_org_id::text;
  alter table knowledge_folders alter column organization_id type uuid using organization_id::uuid;
  alter table knowledge_folders add constraint knowledge_folders_organization_id_fkey
    foreign key (organization_id) references organizations(id) on delete cascade;
end $$;

-- 4. organization_id를 text 파라미터로 받던 RPC를 uuid로 갱신한다.

create or replace function match_knowledge_chunks(
  query_embedding vector,
  match_organization_id uuid,
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
  similarity double precision
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

create or replace function delete_knowledge_folder_keep_contents(
  organization_id_input uuid,
  folder_id_input uuid
)
returns boolean
language plpgsql
as $function$
declare
  deleted_count integer;
begin
  -- 1. 해당 폴더에 들어있던 지식 원본을 미분류로 이동
  update public.knowledge_sources
  set
    folder_id = null,
    updated_at = now()
  where organization_id = organization_id_input
    and folder_id = folder_id_input;

  -- 2. 해당 폴더에 들어있던 지식 청크도 미분류로 이동
  update public.knowledge_chunks
  set folder_id = null
  where organization_id = organization_id_input
    and folder_id = folder_id_input;

  -- 3. 폴더만 삭제
  delete from public.knowledge_folders
  where organization_id = organization_id_input
    and id = folder_id_input;

  get diagnostics deleted_count = row_count;

  return deleted_count > 0;
end;
$function$;
