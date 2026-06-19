# 긴 문서 전체를 AI에게 넣는 것보다
# 질문과 관련 있는 부분만 찾아서 넣는 게 정확하고 빠름

# 청크 경계를 찾을 때 우선적으로 시도할 구분자들.
# 앞에 있을수록 더 자연스러운 경계(문장/줄 끝)이므로 우선 적용한다.
BOUNDARY_SEPARATORS = ["\n", ". ", "! ", "? ", "다. ", "요. ", " "]

# 자연스러운 경계를 찾기 위해 chunk_size 끝에서부터 거꾸로 탐색하는 범위.
BOUNDARY_SEARCH_WINDOW = 100


def find_chunk_end(text: str, start: int, chunk_size: int) -> int:
    """
    [start, start + chunk_size] 범위 안에서 자연스러운 청크 경계를 찾는다.
    문장/줄 끝이나 공백에서 자르면 단어가 중간에 잘리는 것을 줄일 수 있다.
    적절한 경계를 찾지 못하면 chunk_size 위치에서 그대로 자른다.
    """
    hard_end = start + chunk_size

    if hard_end >= len(text):
        return len(text)

    search_start = max(start, hard_end - BOUNDARY_SEARCH_WINDOW)

    for separator in BOUNDARY_SEPARATORS:
        position = text.rfind(separator, search_start, hard_end)

        if position != -1 and position > start:
            return position + len(separator)

    return hard_end


def chunk_text(
    text: str,
    chunk_size: int = 600,
    overlap: int = 80,
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
    clean_text = text.strip()

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
        end = find_chunk_end(clean_text, start, chunk_size)

        # 혹시라도 end가 start보다 앞으로 가거나 같으면 무한 루프 방지
        if end <= start:
            end = min(start + chunk_size, text_length)

        chunk = clean_text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        # 문서 끝까지 도달했으면 종료
        if end >= text_length:
            break

        next_start = end - overlap

        # 다음 시작점이 현재 시작점보다 커지지 않으면 무한 루프 방지
        if next_start <= start:
            next_start = end

        start = next_start

    return chunks