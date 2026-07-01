import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.repositories.organization_ai_settings_repo import (
    ALLOWED_STT_PROVIDERS,
    ALLOWED_TTS_PROVIDERS,
    ALLOWED_VOICE_MODES,
    ALLOWED_VOICE_RESPONSE_STYLES,
    get_ai_settings,
    update_ai_settings,
    validate_model_name,
    validate_stt_provider,
    validate_tts_provider,
    validate_voice_mode,
    validate_voice_response_style,
)


router = APIRouter(
    prefix="/organization-ai-settings",
    tags=["Organization AI Settings"],
)

# 실제로 호출 가능한 모델 목록(코드 상수). .env는 비밀키만 보관하고,
# 선택 가능한 모델/보이스 같은 비밀 아닌 옵션은 여기서 관리한다.
STT_MODELS_BY_PROVIDER = {
    "openai": ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"],
    # CLOVA Speech는 모델 파라미터가 없어 provider 자체가 엔진이지만,
    # 프론트 드롭다운이 빈 목록을 받지 않도록 단일 옵션으로 표시한다.
    "clova": ["clova-speech"],
}

TTS_MODELS_BY_PROVIDER = {
    "openai": ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"],
    "elevenlabs": ["eleven_flash_v2_5", "eleven_multilingual_v2"],
}

# OpenAI는 모델별로 지원 보이스가 다르다(marin/cedar는 gpt-4o-mini-tts 전용).
TTS_VOICES_BY_MODEL = {
    "gpt-4o-mini-tts": [
        "alloy", "ash", "ballad", "coral", "echo", "fable",
        "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar",
    ],
    "tts-1": ["alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"],
    "tts-1-hd": ["alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"],
}

# Realtime은 OpenAI Realtime API만 지원한다.
REALTIME_MODELS = ["gpt-realtime-2"]
REALTIME_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"]

ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v2/voices"


class OrganizationAISettingsUpdateRequest(BaseModel):
    llm_provider: str | None = Field(default=None, example="openai")
    llm_model: str | None = Field(default=None, example="gpt-4.1-mini")
    decision_model: str | None = Field(default=None, example="gpt-4.1-mini")
    voice_enabled: bool | None = None
    voice_mode: str | None = Field(default=None, example="pipeline")
    voice_stt_provider: str | None = Field(default=None, example="openai")
    voice_stt_model: str | None = Field(default=None, example="gpt-4o-mini-transcribe")
    voice_tts_provider: str | None = Field(default=None, example="openai")
    voice_tts_model: str | None = Field(default=None, example="gpt-4o-mini-tts")
    voice_tts_voice: str | None = Field(default=None, example="marin")
    elevenlabs_model: str | None = Field(default=None, example="eleven_flash_v2_5")
    elevenlabs_voice_id: str | None = Field(default=None, example="21m00Tcm4TlvDq8ikWAM")
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
        "elevenlabs_model",
        "elevenlabs_voice_id",
        "realtime_model",
        "realtime_voice",
    ]

    try:
        for field in model_fields:
            if field in raw:
                raw[field] = validate_model_name(raw[field], field)

        if "voice_stt_provider" in raw:
            raw["voice_stt_provider"] = validate_stt_provider(raw["voice_stt_provider"])

        if "voice_tts_provider" in raw:
            raw["voice_tts_provider"] = validate_tts_provider(raw["voice_tts_provider"])

        if "voice_mode" in raw:
            raw["voice_mode"] = validate_voice_mode(raw["voice_mode"])

        if "voice_response_style" in raw:
            raw["voice_response_style"] = validate_voice_response_style(
                raw["voice_response_style"]
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return raw


@router.get("/elevenlabs-voices")
async def list_elevenlabs_voices(search: str | None = None):
    """
    ElevenLabs는 보이스가 계정마다 다르고 voice_id가 고정 영문 코드가 아니라,
    실제 호출 가능한 목록을 코드에 하드코딩할 수 없다. 그래서 ElevenLabs API를
    그대로 중계해 프론트가 실시간으로 선택지를 받게 한다.
    """
    if not settings.elevenlabs_api_key:
        raise HTTPException(status_code=500, detail="ElevenLabs is not configured")

    params = {"page_size": 100}
    if search:
        params["search"] = search

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                ELEVENLABS_VOICES_URL,
                headers={"xi-api-key": settings.elevenlabs_api_key},
                params=params,
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Failed to fetch ElevenLabs voices")

    if not response.is_success:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs voices request failed: {response.text[:300]}",
        )

    data = response.json()
    voices = [
        {
            "voice_id": voice.get("voice_id"),
            "name": voice.get("name"),
            "language": (voice.get("labels") or {}).get("language"),
            "accent": (voice.get("labels") or {}).get("accent"),
            "description": (voice.get("labels") or {}).get("description"),
        }
        for voice in data.get("voices", [])
    ]
    return {"voices": voices, "total_count": data.get("total_count", len(voices))}


@router.get("")
def get_organization_ai_settings(organization_id: str):
    return {
        "organization_id": organization_id,
        "settings": get_ai_settings(organization_id),
        "options": {
            "voice_modes": sorted(ALLOWED_VOICE_MODES),
            "stt_providers": sorted(ALLOWED_STT_PROVIDERS),
            "stt_models_by_provider": STT_MODELS_BY_PROVIDER,
            "tts_providers": sorted(ALLOWED_TTS_PROVIDERS),
            "tts_models_by_provider": TTS_MODELS_BY_PROVIDER,
            "tts_voices_by_model": TTS_VOICES_BY_MODEL,
            "realtime_models": REALTIME_MODELS,
            "realtime_voices": REALTIME_VOICES,
            "elevenlabs_models": TTS_MODELS_BY_PROVIDER["elevenlabs"],
            "elevenlabs_voices_endpoint": "/organization-ai-settings/elevenlabs-voices",
            "voice_response_styles": sorted(ALLOWED_VOICE_RESPONSE_STYLES),
            "recommended_templates": [
                {
                    "name": "저비용 Pipeline",
                    "voice_mode": "pipeline",
                    "voice_stt_provider": "openai",
                    "voice_stt_model": "gpt-4o-mini-transcribe",
                    "voice_tts_provider": "openai",
                    "voice_tts_model": "gpt-4o-mini-tts",
                    "voice_tts_voice": "marin",
                    "voice_response_style": "friendly_short",
                },
                {
                    "name": "자연스러운 한국어 (ElevenLabs)",
                    "voice_mode": "pipeline",
                    "voice_stt_provider": "openai",
                    "voice_stt_model": "gpt-4o-transcribe",
                    "voice_tts_provider": "elevenlabs",
                    "elevenlabs_model": "eleven_flash_v2_5",
                    "elevenlabs_voice_id": "",
                    "voice_response_style": "friendly_short",
                },
                {
                    "name": "CLOVA Speech (한국어 STT)",
                    "voice_mode": "pipeline",
                    "voice_stt_provider": "clova",
                    "voice_tts_provider": "openai",
                    "voice_tts_model": "gpt-4o-mini-tts",
                    "voice_tts_voice": "marin",
                    "voice_response_style": "friendly_short",
                },
                {
                    "name": "Realtime Voice",
                    "voice_mode": "realtime",
                    "voice_tts_provider": "openai",
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
