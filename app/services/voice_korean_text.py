import re

KOREAN_DIGITS = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
KOREAN_SMALL_UNITS = ["", "십", "백", "천"]
KOREAN_BIG_UNITS = ["", "만", "억", "조"]
KOREAN_HOURS = {
    1: "한",
    2: "두",
    3: "세",
    4: "네",
    5: "다섯",
    6: "여섯",
    7: "일곱",
    8: "여덟",
    9: "아홉",
    10: "열",
    11: "열한",
    12: "열두",
}
KOREAN_PHONE_DIGITS = {
    "0": "공",
    "1": "일",
    "2": "이",
    "3": "삼",
    "4": "사",
    "5": "오",
    "6": "육",
    "7": "칠",
    "8": "팔",
    "9": "구",
}
KOREAN_MONTHS = {
    1: "일월",
    2: "이월",
    3: "삼월",
    4: "사월",
    5: "오월",
    6: "유월",
    7: "칠월",
    8: "팔월",
    9: "구월",
    10: "시월",
    11: "십일월",
    12: "십이월",
}


def _read_korean_under_10000(value: int) -> str:
    if value == 0:
        return ""

    parts: list[str] = []
    digits = list(map(int, str(value).zfill(4)))
    for index, digit in enumerate(digits):
        if digit == 0:
            continue
        unit_index = 3 - index
        digit_word = "" if digit == 1 and unit_index > 0 else KOREAN_DIGITS[digit]
        parts.append(f"{digit_word}{KOREAN_SMALL_UNITS[unit_index]}")
    return "".join(parts)


def read_korean_number(value: int) -> str:
    if value == 0:
        return "영"
    if value < 0:
        return f"마이너스 {read_korean_number(abs(value))}"

    groups: list[str] = []
    group_index = 0
    remaining = value
    while remaining > 0 and group_index < len(KOREAN_BIG_UNITS):
        chunk = remaining % 10000
        if chunk:
            chunk_text = _read_korean_under_10000(chunk)
            groups.append(f"{chunk_text}{KOREAN_BIG_UNITS[group_index]}")
        remaining //= 10000
        group_index += 1

    if remaining > 0:
        groups.append(str(remaining))

    return "".join(reversed(groups))


def read_korean_time(hour: int, minute: int) -> str:
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return f"{hour}:{minute:02d}"

    period = "오전" if hour < 12 else "오후"
    display_hour = hour % 12 or 12
    hour_text = KOREAN_HOURS.get(display_hour, read_korean_number(display_hour))
    if minute == 0:
        return f"{period} {hour_text} 시"
    return f"{period} {hour_text} 시 {read_korean_number(minute)} 분"


def read_korean_date(year: int, month: int, day: int) -> str:
    if year < 1 or month < 1 or month > 12 or day < 1 or day > 31:
        return f"{year}-{month:02d}-{day:02d}"

    return f"{KOREAN_MONTHS[month]} {read_korean_number(day)} 일"


def read_phone_number(value: str) -> str:
    groups = re.split(r"[-\s]+", value)
    spoken_groups = [
        "".join(KOREAN_PHONE_DIGITS[digit] for digit in group if digit.isdigit())
        for group in groups
    ]
    return " ".join(group for group in spoken_groups if group)


def normalize_text_for_korean_speech(text: str) -> str:
    """
    TTS가 15:00을 "십오 공공"처럼 읽는 문제를 줄이기 위한 음성용 전처리.
    화면에 보여줄 답변 원문은 바꾸지 않고 TTS 입력에만 사용한다.
    """

    normalized = text

    normalized = re.sub(
        r"(?<!\d)(0\d{1,2})[-\s](\d{3,4})[-\s](\d{4})(?!\d)",
        lambda match: read_phone_number(match.group(0)),
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?!\d)",
        lambda match: read_korean_date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        lambda match: read_korean_date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ),
        normalized,
    )
    normalized = re.sub(
        r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)",
        lambda match: read_korean_time(int(match.group(1)), int(match.group(2))),
        normalized,
    )
    normalized = re.sub(
        r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)\s*원",
        lambda match: f"{read_korean_number(int(match.group(1).replace(',', '')))} 원",
        normalized,
    )
    normalized = re.sub(
        r"(?<![\w.])(\d{1,3}(?:,\d{3})+)(?![\w.])",
        lambda match: read_korean_number(int(match.group(1).replace(",", ""))),
        normalized,
    )

    return normalized


# 스트리밍 TTS용 문장 분할 파라미터.
# delta가 쌓이다 문장 경계를 만나면 그 구간만 먼저 합성해 오디오를 흘려보낸다.
# 숫자 사이의 마침표(1.5)는 경계로 보지 않아 금액/날짜 전처리가 깨지지 않게 한다.
TTS_BOUNDARY_RE = re.compile(r"(?<!\d)[.!?。！？](?!\d)|\n")
MIN_TTS_SEGMENT_CHARS = 12
MAX_TTS_SEGMENT_CHARS = 160


def split_tts_segments(buffer: str, *, flush_all: bool = False) -> tuple[list[str], str]:
    """
    누적된 텍스트 버퍼에서 합성 가능한 문장 구간을 잘라낸다.

    문장 부호를 만나면 그 앞까지를 한 구간으로 떼어내고, 부호 없이 너무 길어지면
    강제로 끊어 첫 오디오가 늦지 않게 한다. flush_all이면 남은 버퍼도 모두 내보낸다.
    """

    segments: list[str] = []
    while True:
        match = next(
            (m for m in TTS_BOUNDARY_RE.finditer(buffer) if m.end() >= MIN_TTS_SEGMENT_CHARS),
            None,
        )
        if match:
            segments.append(buffer[: match.end()])
            buffer = buffer[match.end() :]
            continue
        if len(buffer) >= MAX_TTS_SEGMENT_CHARS:
            segments.append(buffer[:MAX_TTS_SEGMENT_CHARS])
            buffer = buffer[MAX_TTS_SEGMENT_CHARS:]
            continue
        break

    if flush_all and buffer.strip():
        segments.append(buffer)
        buffer = ""

    return segments, buffer
