-- knowledge_folders: 2depth 지원을 위한 parent_id 추가
ALTER TABLE knowledge_folders
  ADD COLUMN IF NOT EXISTS parent_id uuid REFERENCES knowledge_folders(id) ON DELETE CASCADE;

-- depth 2 제한: parent_id의 parent_id가 null이어야 함 (루트 폴더만 부모가 될 수 있음)
ALTER TABLE knowledge_folders
  ADD CONSTRAINT knowledge_folders_max_depth
  CHECK (parent_id IS NULL OR (
    SELECT parent_id FROM knowledge_folders p WHERE p.id = parent_id
  ) IS NULL);

-- knowledge_chunks: 키워드 컬럼 + 한국어 full-text search 인덱스
ALTER TABLE knowledge_chunks
  ADD COLUMN IF NOT EXISTS keywords text[] DEFAULT '{}';

-- tsvector 컬럼 (content 기반 full-text search용)
ALTER TABLE knowledge_chunks
  ADD COLUMN IF NOT EXISTS content_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED;

-- full-text search 인덱스
CREATE INDEX IF NOT EXISTS knowledge_chunks_content_tsv_idx
  ON knowledge_chunks USING gin(content_tsv);

-- keywords 배열 인덱스
CREATE INDEX IF NOT EXISTS knowledge_chunks_keywords_idx
  ON knowledge_chunks USING gin(keywords);

-- 하이브리드 검색 RPC: 벡터 유사도 + 키워드 매칭
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
  WITH vector_results AS (
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
      AND ks.is_referenced = true
      AND (match_folder_id IS NULL OR kc.folder_id = match_folder_id)
    ORDER BY kc.embedding <=> query_embedding
    LIMIT match_count * 3
  ),
  keyword_scores AS (
    SELECT
      vr.*,
      CASE
        WHEN array_length(query_keywords, 1) > 0 AND array_length(vr.keywords, 1) > 0
        THEN (
          SELECT count(*)::float / array_length(query_keywords, 1)
          FROM unnest(query_keywords) qk
          WHERE qk = ANY(vr.keywords)
        )
        ELSE 0.0
      END AS keyword_score
    FROM vector_results vr
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
