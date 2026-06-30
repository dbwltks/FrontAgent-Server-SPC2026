from pathlib import Path
import pandas as pd
from pypdf import PdfReader
import httpx
import trafilatura

def extract_pdf_text(file_path: str) -> str:
    reader = PdfReader(file_path)
    text = ""

    for page in reader.pages:
        text += page.extract_text() or ""
        text += "\n"

    return text.strip()


def extract_txt_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read().strip()


def format_cell_value(value) -> str:
    if pd.isna(value):
        return ""

    # 50000.0 → 50000
    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    return str(value).strip()


def dataframe_to_searchable_text(df: pd.DataFrame) -> str:
    """
    CSV / Excel 같은 표 데이터를 범용 검색 텍스트로 변환한다.

    예:
    service_name, price, duration
    방문 상담, 100000, 90분

    변환:
    1번 항목: service_name은/는 방문 상담, price은/는 100000, duration은/는 90분입니다.
    """

    lines = []

    for row_index, row in df.iterrows():
        parts = []

        for column in df.columns:
            value = format_cell_value(row[column])

            if not value:
                continue

            column_name = str(column).strip()
            parts.append(f"{column_name}은/는 {value}")

        if parts:
            line = f"{row_index + 1}번 항목: " + ", ".join(parts) + "입니다."
            lines.append(line)

    return "\n".join(lines)


def extract_csv_text(file_path: str) -> str:
    df = pd.read_csv(file_path)
    return dataframe_to_searchable_text(df)


def extract_excel_text(file_path: str) -> str:
    df = pd.read_excel(file_path)
    return dataframe_to_searchable_text(df)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

def extract_text_from_url(url: str) -> str:
    """URL을 받아 본문 텍스트만 추출한다. (메뉴·광고·푸터 제거)"""
    response = httpx.get(url, timeout=15, follow_redirects=True, headers=_HEADERS)
    response.raise_for_status()

    text = trafilatura.extract(response.text) or ""
    return text.strip()


def extract_text_from_file(file_path: str, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_text(file_path)

    if suffix in [".txt", ".md"]:
        return extract_txt_text(file_path)

    if suffix == ".csv":
        return extract_csv_text(file_path)

    if suffix in [".xlsx", ".xls"]:
        return extract_excel_text(file_path)

    raise ValueError(f"Unsupported file type: {suffix}")