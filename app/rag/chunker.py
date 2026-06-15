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

        if position != -1:
            return position + len(separator)

    return hard_end


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[str]:
    clean_text = text.strip()

    if not clean_text:
        return []

    chunks = []
    start = 0

    while start < len(clean_text):
        end = find_chunk_end(clean_text, start, chunk_size)
        chunk = clean_text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start = end - overlap

        if start < 0:
            start = 0

        if start >= len(clean_text):
            break

    return chunks