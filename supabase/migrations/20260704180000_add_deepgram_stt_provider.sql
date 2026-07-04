-- Deepgram STT provider를 조직별 voice_stt_provider 선택지에 추가한다.
alter table public.organization_ai_settings
  drop constraint if exists organization_ai_settings_voice_stt_provider_check;

alter table public.organization_ai_settings
  add constraint organization_ai_settings_voice_stt_provider_check
    check (voice_stt_provider in ('openai', 'clova', 'deepgram'));
