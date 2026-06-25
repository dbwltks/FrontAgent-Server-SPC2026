-- Allow the task builder to persist fallback edges used by branch nodes.

ALTER TABLE public.task_edges
  DROP CONSTRAINT IF EXISTS task_edges_edge_type_check;

ALTER TABLE public.task_edges
  ADD CONSTRAINT task_edges_edge_type_check CHECK (
    edge_type IN ('single', 'condition', 'failure', 'fallback')
  );
