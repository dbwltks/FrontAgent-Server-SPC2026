-- ElevenLabs Conversational AI realtime 세션용 agent id를 조직별로 저장한다.
-- 기존 elevenlabs_voice_id는 TTS voice id이고, realtime 대화에는 agent_id가 별도로 필요하다.
alter table public.organization_ai_settings
  add column if not exists elevenlabs_agent_id text;
