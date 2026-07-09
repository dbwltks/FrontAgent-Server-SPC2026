"""
task_flows 트리거 메타(trigger_description, trigger_examples, trigger_intent)로
태스크 시작 여부를 판별한다. agent LLM tool calling을 대체하는 1차 라우터.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# DB trigger_examples가 비어 있을 때 쓰는 org 공통 fallback.
DEFAULT_TRIGGER_EXAMPLES: dict[str, list[str]] = {
    "reservation_create": [
        "예약하고 싶어요",
        "예약해주세요",
        "예약해줘",
        "예약할게요",
        "예약 잡아주세요",
        "어떤 서비스 있어요",
        "무슨 서비스 있어요",
    ],
    "reservation_lookup": [
        "예약 조회",
        "내 예약 확인",
        "예약 확인해줘",
        "예약 내역",
    ],
    "reservation_cancel": [
        "예약 취소",
        "취소해줘",
        "예약 취소해주세요",
    ],
    "reservation_update": [
        "예약 변경",
        "변경해줘",
        "예약 바꿔줘",
    ],
}

MIN_TRIGGER_SCORE = 70.0

_CHANNEL_ALIASES = {
    "web_chat": "chat",
    "chat": "chat",
    "web_call": "voice",
    "voice": "voice",
}


@dataclass(frozen=True)
class TaskTriggerMatch:
    flow_id: str
    task_type: str
    flow_name: str
    match_reason: str
    score: float


def _normalize(text: str) -> str:
    return re.sub(r"[\s?!.,·'\"\"''`]+", "", (text or "").strip().lower())


def _looks_like_policy_question(message: str) -> bool:
    """정책/조건 FAQ는 트리거로 태스크를 시작하지 않는다."""
    msg = message.strip()
    if not msg:
        return False
    has_question = bool(
        re.search(r"(\?|인가요|인가|되나요|될까요|할\s*수\s*있|가능한)", msg)
    )
    has_policy_topic = bool(re.search(r"(가능|되나|정책|취소|변경|환불|수수료)", msg))
    has_action = bool(
        re.search(
            r"(해줘|해주|할게|하고\s*싶|부탁|신청|예약\s*해|잡아"
            r"|할\s*수\s*있(을까요|나요|어요)?|가능(할까요|한가요)?)",
            msg,
        )
    )
    return has_question and has_policy_topic and not has_action


def _parse_trigger_examples(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return [text]
            raw = parsed
        else:
            return [text]
    if not isinstance(raw, list):
        return []
    examples: list[str] = []
    for item in raw:
        if item is None:
            continue
        value = str(item).strip()
        if value:
            examples.append(value)
    return examples


def _collect_examples(flow: dict[str, Any]) -> list[str]:
    examples = _parse_trigger_examples(flow.get("trigger_examples"))
    intent = str(flow.get("trigger_intent") or "").strip()
    for fallback in DEFAULT_TRIGGER_EXAMPLES.get(intent, []):
        if fallback not in examples:
            examples.append(fallback)
    return examples


def _channel_allowed(flow: dict[str, Any], channel: str | None) -> bool:
    if not channel:
        return True
    normalized = _CHANNEL_ALIASES.get(channel, channel)
    allowed = flow.get("allowed_channels") or ["chat", "voice"]
    if isinstance(allowed, str):
        try:
            allowed = json.loads(allowed)
        except json.JSONDecodeError:
            allowed = [allowed]
    if not isinstance(allowed, list):
        return True
    return normalized in allowed or channel in allowed


def _score_example(message_norm: str, example: str) -> tuple[float, str]:
    example_norm = _normalize(example)
    if not example_norm or not message_norm:
        return 0.0, ""

    if example_norm == message_norm:
        return 100.0, f"example:{example}"
    if example_norm in message_norm:
        coverage = len(example_norm) / max(len(message_norm), 1)
        return 90.0 + min(coverage * 10.0, 9.0), f"example:{example}"
    if message_norm in example_norm:
        return 75.0, f"example:{example}"
    return 0.0, ""


def _score_flow(message: str, flow: dict[str, Any]) -> tuple[float, str]:
    message_norm = _normalize(message)
    if not message_norm:
        return 0.0, ""

    best_score = 0.0
    best_reason = ""
    for example in _collect_examples(flow):
        score, reason = _score_example(message_norm, example)
        if score > best_score:
            best_score = score
            best_reason = reason

    return best_score, best_reason


def match_task_trigger(
    user_message: str,
    flows: list[dict[str, Any]],
    *,
    channel: str | None = None,
) -> TaskTriggerMatch | None:
    """
    enabled task_flows 중 사용자 메시지와 트리거 examples가 매칭되는 flow를 반환.
    """
    message = (user_message or "").strip()
    if not message or not flows:
        return None

    if _looks_like_policy_question(message):
        return None

    best_flow: dict[str, Any] | None = None
    best_score = 0.0
    best_reason = ""

    for flow in flows:
        if flow.get("is_enabled") is False:
            continue
        if not _channel_allowed(flow, channel):
            continue

        score, reason = _score_flow(message, flow)
        if score > best_score:
            best_flow = flow
            best_score = score
            best_reason = reason

    if not best_flow or best_score < MIN_TRIGGER_SCORE:
        return None

    task_type = str(best_flow.get("trigger_intent") or "reservation_create").strip()
    return TaskTriggerMatch(
        flow_id=str(best_flow["id"]),
        task_type=task_type or "reservation_create",
        flow_name=str(best_flow.get("name") or ""),
        match_reason=best_reason,
        score=best_score,
    )
