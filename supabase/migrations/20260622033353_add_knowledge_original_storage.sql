alter table public.knowledge_sources
  add column if not exists storage_bucket text,
  add column if not exists storage_path text,
  add column if not exists file_size bigint,
  add column if not exists checksum_sha256 text;

create unique index if not exists knowledge_sources_storage_object_key
  on public.knowledge_sources (storage_bucket, storage_path)
  where storage_bucket is not null and storage_path is not null;

alter table public.knowledge_chunks
  drop constraint if exists knowledge_chunks_source_id_fkey;

alter table public.knowledge_chunks
  add constraint knowledge_chunks_source_id_fkey
  foreign key (source_id)
  references public.knowledge_sources(id)
  on delete cascade;

insert into storage.buckets (
  id,
  name,
  public,
  file_size_limit,
  allowed_mime_types
)
values (
  'knowledge-originals',
  'knowledge-originals',
  false,
  20971520,
  array[
    'application/pdf',
    'text/plain',
    'text/markdown',
    'text/csv',
    'application/csv',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  ]
)
on conflict (id) do update
set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
