from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Front Agent AI Core"
    app_env: str = "local"
    app_version: str = "0.1.0"

    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"
    openai_realtime_model: str = "gpt-realtime-2"
    openai_realtime_voice: str = "marin"
    voice_mode: str = "pipeline"
    voice_stt_model: str = "gpt-4o-mini-transcribe"
    voice_tts_model: str = "gpt-4o-mini-tts"
    voice_tts_voice: str = "marin"
    voice_upload_max_bytes: int = 10 * 1024 * 1024

    # TTS provider 선택: "openai" | "elevenlabs". org 설정(voice_tts_provider)으로 덮어쓸 수 있다.
    tts_provider: str = "openai"
    elevenlabs_api_key: str = ""
    # Flash v2.5: 저지연 + 다국어(한국어). 자연스러움 우선이면 eleven_multilingual_v2.
    elevenlabs_model: str = "eleven_flash_v2_5"
    # 한국어에 어울리는 보이스 ID를 ElevenLabs에서 골라 지정한다(미설정 시 합성 실패).
    elevenlabs_voice_id: str = ""

    redis_url: str = "redis://localhost:6379"

    supabase_url: str
    supabase_service_role_key: str

    # LangGraph checkpointer가 사용하는 직접 Postgres 연결 (Session pooler)
    database_url: str

    knowledge_upload_max_bytes: int = 20 * 1024 * 1024
    knowledge_storage_bucket: str = "knowledge-originals"

    class Config:
        env_file = ".env"


settings = Settings()
