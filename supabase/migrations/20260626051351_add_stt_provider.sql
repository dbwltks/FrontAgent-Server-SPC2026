-- STT provider를 조직별로 openai/clova 중 선택할 수 있게 컬럼을 추가한다.
-- 기본값은 기존 동작과 동일한 openai이므로 마이그레이션만으로 기존 조직 동작은 바뀌지 않는다.
alter table public.organization_ai_settings
  add column if not exists voice_stt_provider text not null default 'openai'
    check (voice_stt_provider in ('openai', 'clova'));
