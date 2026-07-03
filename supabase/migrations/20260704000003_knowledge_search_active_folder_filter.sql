-- RAG 검색: 소스 indexed+referenced, 폴더 is_active=true(미분류는 허용)만 검색 대상.

ALTER TABLE knowledge_folders
  ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS knowledge_folders_org_active_idx
  ON knowledge_folders (organization_id, is_active);

CREATE OR REPLACE FUNCTION match_knowledge_chunks(
  query_embedding vector,
  match_organization_id uuid,
  match_count integer,
  match_folder_id uuid DEFAULT NULL
)
RETURNS TABLE (
  id uuid,
  source_id uuid,
  source_title text,
  folder_id uuid,
  content text,
  metadata jsonb,
  similarity double precision
)
LANGUAGE sql
AS $$
  SELECT
    kc.id,
    kc.source_id,
    ks.title AS source_title,
    kc.folder_id,
    kc.content,
    kc.metadata,
    1 - (kc.embedding <=> query_embedding) AS similarity
  FROM public.knowledge_chunks kc
  JOIN public.knowledge_sources ks ON ks.id = kc.source_id
  WHERE kc.organization_id = match_organization_id
    AND kc.embedding IS NOT NULL
    AND ks.status = 'indexed'
    AND ks.is_referenced = true
    AND (
      kc.folder_id IS NULL
      OR EXISTS (
        SELECT 1
        FROM public.knowledge_folders kf
        WHERE kf.id = kc.folder_id
          AND kf.organization_id = match_organization_id
          AND kf.is_active = true
      )
    )
    AND (
      match_folder_id IS NULL
      OR kc.folder_id = match_folder_id
    )
  ORDER BY kc.embedding <=> query_embedding
  LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION match_knowledge_chunks_hybrid(
  query_embedding vector(1536),
  query_keywords text[],
  match_organization_id uuid,
  match_count int DEFAULT 5,
  match_folder_id uuid DEFAULT NULL,
  vector_weight float DEFAULT 0.7,
  keyword_weight float DEFAULT 0.3
)
RETURNS TABLE (
  id uuid,
  source_id uuid,
  source_title text,
  folder_id uuid,
  content text,
  metadata jsonb,
  keywords text[],
  similarity float
)
LANGUAGE sql
AS $$
  WITH filtered_keywords AS (
    SELECT array_agg(DISTINCT lower(k)) AS kws
    FROM unnest(coalesce(query_keywords, '{}'::text[])) AS k
    WHERE length(k) >= 2
  ),
  fts_keywords AS (
    SELECT array_agg(DISTINCT qk) AS kws
    FROM filtered_keywords fk,
      unnest(fk.kws) qk
    WHERE qk !~ '[[:space:]]' AND qk !~ '[^a-zA-Z0-9가-힣]'
  ),
  vector_results AS (
    SELECT
      kc.id,
      kc.source_id,
      ks.title AS source_title,
      kc.folder_id,
      kc.content,
      kc.metadata,
      kc.keywords,
      1 - (kc.embedding <=> query_embedding) AS vector_score
    FROM knowledge_chunks kc
    JOIN knowledge_sources ks ON ks.id = kc.source_id
    WHERE
      kc.organization_id = match_organization_id
      AND kc.embedding IS NOT NULL
      AND ks.status = 'indexed'
      AND ks.is_referenced = true
      AND (
        kc.folder_id IS NULL
        OR EXISTS (
          SELECT 1
          FROM knowledge_folders kf
          WHERE kf.id = kc.folder_id
            AND kf.organization_id = match_organization_id
            AND kf.is_active = true
        )
      )
      AND (match_folder_id IS NULL OR kc.folder_id = match_folder_id)
    ORDER BY kc.embedding <=> query_embedding
    LIMIT match_count * 5
  ),
  lexical_results AS (
    SELECT
      kc.id,
      kc.source_id,
      ks.title AS source_title,
      kc.folder_id,
      kc.content,
      kc.metadata,
      kc.keywords,
      0.0::float AS vector_score
    FROM knowledge_chunks kc
    JOIN knowledge_sources ks ON ks.id = kc.source_id
    CROSS JOIN filtered_keywords fk
    WHERE
      kc.organization_id = match_organization_id
      AND kc.embedding IS NOT NULL
      AND ks.status = 'indexed'
      AND ks.is_referenced = true
      AND (
        kc.folder_id IS NULL
        OR EXISTS (
          SELECT 1
          FROM knowledge_folders kf
          WHERE kf.id = kc.folder_id
            AND kf.organization_id = match_organization_id
            AND kf.is_active = true
        )
      )
      AND (match_folder_id IS NULL OR kc.folder_id = match_folder_id)
      AND fk.kws IS NOT NULL
      AND EXISTS (
        SELECT 1
        FROM unnest(fk.kws) qk
        WHERE kc.content ILIKE ('%' || qk || '%')
      )
  ),
  fts_results AS (
    SELECT
      kc.id,
      kc.source_id,
      ks.title AS source_title,
      kc.folder_id,
      kc.content,
      kc.metadata,
      kc.keywords,
      0.0::float AS vector_score
    FROM knowledge_chunks kc
    JOIN knowledge_sources ks ON ks.id = kc.source_id
    CROSS JOIN fts_keywords fk
    WHERE
      kc.organization_id = match_organization_id
      AND kc.embedding IS NOT NULL
      AND ks.status = 'indexed'
      AND ks.is_referenced = true
      AND (
        kc.folder_id IS NULL
        OR EXISTS (
          SELECT 1
          FROM knowledge_folders kf
          WHERE kf.id = kc.folder_id
            AND kf.organization_id = match_organization_id
            AND kf.is_active = true
        )
      )
      AND (match_folder_id IS NULL OR kc.folder_id = match_folder_id)
      AND fk.kws IS NOT NULL
      AND array_length(fk.kws, 1) > 0
      AND kc.content_tsv @@ to_tsquery(
        'simple',
        (
          SELECT string_agg(replace(qk, '''', ''''''), ' | ')
          FROM unnest(fk.kws) qk
        )
      )
  ),
  combined AS (
    SELECT DISTINCT ON (id)
      id,
      source_id,
      source_title,
      folder_id,
      content,
      metadata,
      keywords,
      vector_score
    FROM (
      SELECT * FROM vector_results
      UNION ALL
      SELECT * FROM lexical_results
      UNION ALL
      SELECT * FROM fts_results
    ) all_candidates
    ORDER BY id, vector_score DESC
  ),
  keyword_scores AS (
    SELECT
      c.*,
      CASE
        WHEN fk.kws IS NOT NULL AND array_length(fk.kws, 1) > 0
        THEN (
          SELECT count(*)::float / array_length(fk.kws, 1)
          FROM unnest(fk.kws) qk
          WHERE
            lower(qk) = ANY(
              SELECT lower(k) FROM unnest(coalesce(c.keywords, '{}'::text[])) k
            )
            OR c.content ILIKE ('%' || qk || '%')
        )
        ELSE 0.0
      END AS keyword_score
    FROM combined c
    CROSS JOIN filtered_keywords fk
  )
  SELECT
    id,
    source_id,
    source_title,
    folder_id,
    content,
    metadata,
    keywords,
    (vector_weight * vector_score + keyword_weight * keyword_score)::float AS similarity
  FROM keyword_scores
  ORDER BY similarity DESC
  LIMIT match_count;
$$;
