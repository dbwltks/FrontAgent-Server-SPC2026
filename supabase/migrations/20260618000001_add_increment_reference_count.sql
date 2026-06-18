create or replace function increment_knowledge_reference_count(source_id_input uuid)
returns void
language sql
as $$
  update public.knowledge_sources
  set reference_count = reference_count + 1
  where id = source_id_input;
$$;
