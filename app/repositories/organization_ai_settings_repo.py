import logging

from app.core.config import settings
from app.core.db import supabase


logger = logging.getLogger(__name__)

ALLOWED_VOICE_MODES = {"pipeline", "realtime"}
ALLOWED_VOICE_RESPONSE_STYLES = {
    "friendly_short",
    "professional_short",
    "casual_short",
}
MISSING_TABLE_CODE = "PGRST205"


def _is_missing_table_error(error: Exception) -> bool:
    return MISSING_TABLE_CODE in str(error)


def default_ai_settings(organization_id: str) -> dict:
    voice_mode = settings.voice_mode.strip().lower()

    return {
        "organization_id": organization_id,
        "llm_provider": "openai",
        "llm_model": settings.openai_model,
        "decision_model": None,
        "voice_enabled": True,
        "voice_mode": voice_mode if voice_mode in ALLOWED_VOICE_MODES else "pipeline",
        "voice_stt_model": settings.voice_stt_model,
        "voice_tts_model": settings.voice_tts_model,
        "voice_tts_voice": settings.voice_tts_voice,
        "realtime_model": settings.openai_realtime_model,
        "realtime_voice": settings.openai_realtime_voice,
        "voice_response_style": "friendly_short",
        "monthly_budget_limit_cents": None,
        "monthly_token_limit": None,
    }


def _merge_with_defaults(organization_id: str, data: dict | None) -> dict:
    merged = default_ai_settings(organization_id)
    if data:
        merged.update(data)
    return merged


def get_ai_settings(organization_id: str) -> dict:
    try:
        result = (
            supabase.table("organization_ai_settings")
            .select("*")
            .eq("organization_id", organization_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc):
            logger.warning(
                "organization_ai_settings table is missing; using env defaults. "
                "Run the Supabase migration before using organization-level AI settings."
            )
        else:
            logger.warning("failed to fetch organization ai settings", exc_info=True)
        return default_ai_settings(organization_id)

    if result.data:
        return _merge_with_defaults(organization_id, result.data[0])

    created = create_ai_settings(organization_id)
    return _merge_with_defaults(organization_id, created)


def create_ai_settings(organization_id: str) -> dict | None:
    try:
        result = (
            supabase.table("organization_ai_settings")
            .insert(default_ai_settings(organization_id))
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc):
            logger.warning(
                "organization_ai_settings table is missing; cannot persist defaults yet."
            )
        else:
            logger.warning("failed to create organization ai settings", exc_info=True)
        return None

    if not result.data:
        return None

    return result.data[0]


def update_ai_settings(organization_id: str, data: dict) -> dict | None:
    existing = get_ai_settings(organization_id)

    try:
        result = (
            supabase.table("organization_ai_settings")
            .upsert(
                {
                    **existing,
                    **data,
                    "organization_id": organization_id,
                },
                on_conflict="organization_id",
            )
            .execute()
        )
    except Exception as exc:
        if _is_missing_table_error(exc):
            logger.warning(
                "organization_ai_settings table is missing; cannot update settings yet."
            )
        else:
            logger.warning("failed to update organization ai settings", exc_info=True)
        return None

    if not result.data:
        return None

    return _merge_with_defaults(organization_id, result.data[0])


def validate_model_name(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if len(cleaned) > 120:
        raise ValueError(f"{field_name} is too long")
    if any(char.isspace() for char in cleaned):
        raise ValueError(f"{field_name} cannot contain whitespace")

    return cleaned


def validate_voice_mode(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip().lower()
    if cleaned not in ALLOWED_VOICE_MODES:
        raise ValueError("voice_mode must be pipeline or realtime")
    return cleaned


def validate_voice_response_style(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if cleaned not in ALLOWED_VOICE_RESPONSE_STYLES:
        raise ValueError(
            "voice_response_style must be friendly_short, professional_short, or casual_short"
        )
    return cleaned
