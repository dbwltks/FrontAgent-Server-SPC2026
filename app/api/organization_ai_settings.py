from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repositories.organization_ai_settings_repo import (
    ALLOWED_VOICE_MODES,
    ALLOWED_VOICE_RESPONSE_STYLES,
    get_ai_settings,
    update_ai_settings,
    validate_model_name,
    validate_voice_mode,
    validate_voice_response_style,
)


router = APIRouter(
    prefix="/organization-ai-settings",
    tags=["Organization AI Settings"],
)


class OrganizationAISettingsUpdateRequest(BaseModel):
    llm_provider: str | None = Field(default=None, example="openai")
    llm_model: str | None = Field(default=None, example="gpt-4.1-mini")
    decision_model: str | None = Field(default=None, example="gpt-4.1-mini")
    voice_enabled: bool | None = None
    voice_mode: str | None = Field(default=None, example="pipeline")
    voice_stt_model: str | None = Field(default=None, example="gpt-4o-mini-transcribe")
    voice_tts_model: str | None = Field(default=None, example="gpt-4o-mini-tts")
    voice_tts_voice: str | None = Field(default=None, example="marin")
    realtime_model: str | None = Field(default=None, example="gpt-realtime-2")
    realtime_voice: str | None = Field(default=None, example="marin")
    voice_response_style: str | None = Field(default=None, example="friendly_short")
    monthly_budget_limit_cents: int | None = Field(default=None, ge=0)
    monthly_token_limit: int | None = Field(default=None, ge=0)


def model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def normalize_update_data(req: OrganizationAISettingsUpdateRequest) -> dict:
    raw = {
        key: value
        for key, value in model_to_dict(req).items()
        if value is not None
    }

    model_fields = [
        "llm_provider",
        "llm_model",
        "decision_model",
        "voice_stt_model",
        "voice_tts_model",
        "voice_tts_voice",
        "realtime_model",
        "realtime_voice",
    ]

    try:
        for field in model_fields:
            if field in raw:
                raw[field] = validate_model_name(raw[field], field)

        if "voice_mode" in raw:
            raw["voice_mode"] = validate_voice_mode(raw["voice_mode"])

        if "voice_response_style" in raw:
            raw["voice_response_style"] = validate_voice_response_style(
                raw["voice_response_style"]
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return raw


@router.get("")
def get_organization_ai_settings(organization_id: str):
    return {
        "organization_id": organization_id,
        "settings": get_ai_settings(organization_id),
        "options": {
            "voice_modes": sorted(ALLOWED_VOICE_MODES),
            "voice_response_styles": sorted(ALLOWED_VOICE_RESPONSE_STYLES),
            "recommended_templates": [
                {
                    "name": "저비용 Pipeline",
                    "voice_mode": "pipeline",
                    "voice_stt_model": "gpt-4o-mini-transcribe",
                    "voice_tts_model": "gpt-4o-mini-tts",
                    "voice_tts_voice": "marin",
                    "voice_response_style": "friendly_short",
                },
                {
                    "name": "Realtime Voice",
                    "voice_mode": "realtime",
                    "realtime_model": "gpt-realtime-2",
                    "realtime_voice": "marin",
                    "voice_response_style": "friendly_short",
                },
            ],
        },
    }


@router.patch("")
def patch_organization_ai_settings(
    organization_id: str,
    req: OrganizationAISettingsUpdateRequest,
):
    data = normalize_update_data(req)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = update_ai_settings(organization_id, data)
    if not updated:
        raise HTTPException(status_code=500, detail="AI settings update failed")

    return updated
