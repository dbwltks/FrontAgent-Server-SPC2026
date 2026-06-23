import logging
import threading

from app.core.db import supabase


logger = logging.getLogger(__name__)


def create_usage_log(data: dict) -> dict | None:
    try:
        result = supabase.table("ai_usage_logs").insert(data).execute()
    except Exception:
        logger.warning("failed to save ai usage log", exc_info=True)
        return None

    if not result.data:
        return None

    return result.data[0]


def create_usage_log_background(data: dict) -> None:
    threading.Thread(
        target=create_usage_log,
        args=(data,),
        daemon=True,
    ).start()
