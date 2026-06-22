from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Front Agent AI Core"
    app_env: str = "local"
    app_version: str = "0.1.0"

    openai_api_key: str
    openai_model: str = "gpt-4.1-mini"

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
