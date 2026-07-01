from app.api.voice import build_realtime_session_config
from app.api.web_call import (
    _is_meaningful_realtime_message,
    build_web_call_realtime_session_config,
)


def test_voice_realtime_vad_is_conservative():
    config = build_realtime_session_config(
        {"realtime_model": "gpt-realtime-2", "realtime_voice": "marin"}
    )

    turn_detection = config["audio"]["input"]["turn_detection"]
    assert turn_detection["threshold"] == 0.8
    assert turn_detection["silence_duration_ms"] == 1000
    assert config["tool_choice"] == "auto"
    assert "빈 문자열이면 아무 말도 하지 않는다" in config["instructions"]


def test_web_call_realtime_vad_is_conservative():
    config = build_web_call_realtime_session_config(
        {"realtime_model": "gpt-realtime-2", "realtime_voice": "marin"}
    )

    turn_detection = config["audio"]["input"]["turn_detection"]
    assert turn_detection["threshold"] == 0.8
    assert turn_detection["silence_duration_ms"] == 1000
    assert config["tool_choice"] == "auto"
    assert "실제 발화가 아닌 입력에는 도구를 호출하지 않고" in config["instructions"]


def test_realtime_message_filter_rejects_noise_like_inputs():
    assert not _is_meaningful_realtime_message("")
    assert not _is_meaningful_realtime_message("음")
    assert not _is_meaningful_realtime_message("  네  ")


def test_realtime_message_filter_allows_meaningful_inputs():
    assert _is_meaningful_realtime_message("예약하고 싶어요")
    assert _is_meaningful_realtime_message("가격이 얼마예요?")
