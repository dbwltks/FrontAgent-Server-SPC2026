# 긴 문서 전체를 AI에게 넣는 것보다
# 질문과 관련 있는 부분만 찾아서 넣는 게 정확하고 빠름

import re

# 청크 경계를 찾을 때 우선적으로 시도할 구분자들.
# 앞에 있을수록 더 자연스러운 경계(문장/줄 끝)이므로 우선 적용한다.
BOUNDARY_SEPARATORS = ["\n", ". ", "! ", "? ", "다. ", "요. ", " "]

# 자연스러운 경계를 찾기 위해 chunk_size 끝에서부터 거꾸로 탐색하는 범위.
BOUNDARY_SEARCH_WINDOW = 100

# "## 서비스 아이템: A" 같은 레벨 1~2 마크다운 헤더를 청크 경계로 사용한다.
# 레벨 3 이하(###, ####...)는 소제목으로 같은 항목 안에 묶어야 하므로 경계로
# 쓰지 않는다. 예: "화장실 청소" 항목 안의 ### 기본 가격, ### 서비스 설명 등은
# 모두 한 청크에 포함돼야 "화장실 청소 얼마에요?" 질문과 연결된다.
HEADING_PATTERN = re.compile(r"^#{1,2}\s+\S", re.MULTILINE)


def find_next_heading_start(text: str, start: int) -> int | None:
    """
    start 이후(자기 자신 줄 제외)에 등장하는 다음 마크다운 헤더의 시작
    위치를 찾는다. start 위치 자체가 헤더 줄이면 그 줄은 건너뛰고 찾는다.
    """
    search_from = start
    match_at_start = HEADING_PATTERN.match(text, start)
    if match_at_start:
        newline_pos = text.find("\n", start)
        if newline_pos == -1:
            return None
        search_from = newline_pos + 1

    match = HEADING_PATTERN.search(text, search_from)
    return match.start() if match else None


def find_chunk_end(text: str, start: int, chunk_size: int) -> tuple[int, bool]:
    """
    [start, start + chunk_size] 범위 안에서 자연스러운 청크 경계를 찾는다.
    문장/줄 끝이나 공백에서 자르면 단어가 중간에 잘리는 것을 줄일 수 있다.
    적절한 경계를 찾지 못하면 chunk_size 위치에서 그대로 자른다.

    반환값의 두 번째 항목은 헤더 경계로 끊었는지 여부다 - 헤더로 끊은
    chunk는 이미 의미 단위(항목)로 완결됐으므로 호출자가 overlap을 건너뛰어
    다음 항목 일부가 다시 섞여 들어가는 것을 막는다.
    """
    hard_end = start + chunk_size

    # 다음 헤더가 chunk_size 범위 안에 있으면 거기서 끊어 항목 단위를
    # 유지한다. 문서 끝이 chunk_size보다 가까워도(남은 텍스트에 헤더가 여러
    # 개 있을 수 있으므로) 먼저 확인한다. 범위를 벗어나면(헤더 사이 본문이
    # chunk_size보다 긴 경우) 아래의 일반 문장 경계 탐색으로 넘어간다.
    next_heading = find_next_heading_start(text, start)
    if next_heading is not None and start < next_heading <= hard_end:
        return next_heading, True

    if hard_end >= len(text):
        return len(text), False

    search_start = max(start, hard_end - BOUNDARY_SEARCH_WINDOW)

    for separator in BOUNDARY_SEPARATORS:
        position = text.rfind(separator, search_start, hard_end)

        if position != -1 and position > start:
            return position + len(separator), False

    return hard_end, False


def chunk_text(
    text: str,
    chunk_size: int = 1200,
    overlap: int = 100,
) -> list[str]:
    """
    텍스트를 일정 크기의 chunk로 나눈다.

    중요:
    기존 코드처럼 마지막 chunk 이후에도 start = end - overlap을 계속 적용하면
    start가 같은 위치로 반복되어 무한 루프가 발생할 수 있다.

    그래서 아래 조건을 반드시 지킨다.
    - end가 문서 끝에 도달하면 즉시 종료
    - 다음 start가 이전 start보다 커지지 않으면 강제로 종료
    - overlap은 chunk_size보다 작아야 함
    """
    # Windows 줄바꿈(\r\n)으로 작성된 원본 파일을 그대로 저장하면 chunk
    # 내용에 \r(캐리지리턴)이 남아 JSON 응답에서 제어 문자 에러를 일으킨다
    # (실측 사례) - \n으로 정규화해 저장 단계부터 막는다.
    clean_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    if not clean_text:
        return []

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")

    if overlap < 0:
        raise ValueError("overlap must be greater than or equal to 0")

    if overlap >= chunk_size:
        overlap = max(chunk_size // 5, 0)

    chunks: list[str] = []
    start = 0
    text_length = len(clean_text)

    while start < text_length:
        end, cut_at_heading = find_chunk_end(clean_text, start, chunk_size)

        # 혹시라도 end가 start보다 앞으로 가거나 같으면 무한 루프 방지
        if end <= start:
            end = min(start + chunk_size, text_length)

        chunk = clean_text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        # 문서 끝까지 도달했으면 종료
        if end >= text_length:
            break

        # 헤더 경계로 끊은 chunk는 이미 항목 단위로 완결됐으므로 overlap을
        # 적용하지 않는다 - 적용하면 다음 chunk 시작이 이전 항목 끝부분으로
        # 되돌아가 두 항목이 다시 섞인다(실측 사례).
        next_start = end if cut_at_heading else end - overlap

        # 다음 시작점이 현재 시작점보다 커지지 않으면 무한 루프 방지
        if next_start <= start:
            next_start = end

        start = next_start

    return chunks