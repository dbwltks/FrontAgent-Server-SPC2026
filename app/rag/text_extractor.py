"""
파일/URL에서 텍스트를 추출한다.

라이브러리:
- markitdown (Microsoft, MIT): PDF/Word/Excel/PPT/HTML 등 구조화 문서를
  마크다운으로 변환. 테이블·레이아웃 보존 우수.
  https://github.com/microsoft/markitdown
- pandas: CSV 파싱 및 서비스 카탈로그 자연어 변환
- trafilatura: 웹 페이지 본문 추출 (메뉴·광고·푸터 제거)
- httpx: URL 다운로드

파일 형식별 전략:
- CSV: 서비스 카탈로그 패턴이면 서비스별 자연어로 변환, 일반 표는 마크다운 테이블
- PDF/DOCX/PPTX/XLSX 등: markitdown으로 마크다운 변환
- TXT/MD: 원문 그대로
"""

from pathlib import Path

import httpx
import pandas as pd
import trafilatura
from markitdown import MarkItDown

_md = MarkItDown()

# ── CSV 전용 서비스 카탈로그 변환 ─────────────────────────────────────────────

_SERVICE_CATALOG_COLS = {"item_name", "service_name", "base_price", "price"}
_MAX_CELL_LEN = 300  # 셀 값이 이 이상이면 base64/바이너리로 간주해 자름


def _clean_cell(value) -> str:
    if not pd.notna(value):
        return ""
    text = str(value).strip()
    if text.startswith("data:"):
        return "[이미지/파일 데이터]"
    if len(text) > _MAX_CELL_LEN:
        return text[:_MAX_CELL_LEN] + "..."
    return text


def _is_service_catalog(df: pd.DataFrame) -> bool:
    cols = {c.lower().strip() for c in df.columns}
    return bool(cols & _SERVICE_CATALOG_COLS)


def _format_num(value: str) -> str:
    try:
        return f"{int(float(value)):,}"
    except (ValueError, TypeError):
        return value


def _get(row: pd.Series, col_map: dict, *keys: str) -> str:
    for k in keys:
        if k in col_map:
            v = row[col_map[k]]
            if pd.notna(v) and str(v).strip():
                return _clean_cell(v)
    return ""


def _service_catalog_to_text(df: pd.DataFrame) -> str:
    col = {c.lower().strip(): c for c in df.columns}

    services: dict[str, dict] = {}
    for _, row in df.iterrows():
        item = _get(row, col, "item_name", "name")
        if not item:
            continue
        if item not in services:
            services[item] = {
                "description": _get(row, col, "item_description", "description"),
                "base_price": _get(row, col, "base_price", "price"),
                "duration": _get(row, col, "duration_minutes", "duration"),
                "options": [],
            }
        opt_value = _get(row, col, "option_value")
        if opt_value:
            services[item]["options"].append({
                "value": opt_value,
                "desc": _get(row, col, "option_description"),
                "price": _get(row, col, "additional_price"),
                "duration": _get(row, col, "additional_duration"),
            })

    lines = []
    for item_name, info in services.items():
        parts = [f"### 서비스 아이템: {item_name}"]
        if info["description"]:
            parts.append(f"설명: {info['description']}")
        if info["base_price"]:
            parts.append(f"기본 가격: {_format_num(info['base_price'])}원")
        if info["duration"]:
            parts.append(f"소요 시간: {info['duration']}분")
        if info["options"]:
            parts.append("옵션:")
            for opt in info["options"]:
                line = f"  - {opt['value']}"
                if opt["desc"]:
                    line += f": {opt['desc']}"
                if opt["price"]:
                    line += f" (+{_format_num(opt['price'])}원)"
                if opt["duration"]:
                    line += f" (+{opt['duration']}분)"
                parts.append(line)
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def _row_to_natural_sentence(row: pd.Series, cols: list[str], title: str | None = None) -> str:
    """
    행 하나를 자연스러운 한국어 문장으로 변환한다.
    컬럼명 패턴을 보고 적절한 문장 구조를 선택한다.
    """
    vals = {col: _clean_cell(row[col]) for col in cols}
    vals = {k: v for k, v in vals.items() if v and v.lower() not in ("nan", "none", "-", "")}

    if not vals:
        return ""

    prefix = f"[{title}] " if title else ""

    col_lower = {k.lower(): (k, v) for k, v in vals.items()}

    # 재정/거래 데이터 패턴
    date = col_lower.get("날짜", (None, None))[1]
    amount = col_lower.get("금액", (None, None))[1]
    currency = col_lower.get("통화", (None, None))[1]
    category = col_lower.get("카테고리", (None, None))[1]
    desc = col_lower.get("설명", (None, None))[1]
    payment = col_lower.get("결제수단", (None, None))[1]
    vendor = col_lower.get("거래처", (None, None))[1]
    person = col_lower.get("거래자", (None, None))[1]
    status = col_lower.get("정산여부", (None, None))[1]
    tx_type = col_lower.get("유형", (None, None))[1]

    if date and amount:
        # 거래 데이터
        parts = []
        if date:
            parts.append(date)
        if tx_type:
            parts.append(tx_type)
        if vendor:
            parts.append(f"{vendor}에서")
        if desc:
            parts.append(f"{desc}으로")
        if amount:
            amt = f"{amount} {currency}" if currency else amount
            parts.append(f"{amt}를")
        if payment:
            parts.append(f"{payment}로 결제했습니다.")
        extras = []
        if person:
            extras.append(f"담당: {person}")
        if status:
            extras.append(status)
        if category:
            extras.append(f"분류: {category}")
        sentence = prefix + " ".join(parts)
        if extras:
            sentence += " " + ", ".join(extras) + "."
        return sentence

    # 서비스/상품 데이터 패턴
    name = (col_lower.get("이름") or col_lower.get("상품명") or
            col_lower.get("서비스명") or col_lower.get("항목"))[1] if (
        col_lower.get("이름") or col_lower.get("상품명") or
        col_lower.get("서비스명") or col_lower.get("항목")) else None
    price = (col_lower.get("가격") or col_lower.get("금액") or col_lower.get("비용"))[1] if (
        col_lower.get("가격") or col_lower.get("금액") or col_lower.get("비용")) else None

    if name and price:
        parts = [f"{prefix}{name}"]
        if price:
            parts.append(f"가격은 {price}입니다.")
        remaining = {k: v for k, v in vals.items()
                     if k not in {c for c, _ in [col_lower.get("이름", (None,None)),
                                                   col_lower.get("상품명", (None,None)),
                                                   col_lower.get("서비스명", (None,None)),
                                                   col_lower.get("가격", (None,None)),
                                                   col_lower.get("금액", (None,None))] if c}}
        for k, v in list(remaining.items())[:3]:
            parts.append(f"{k}: {v}")
        return " ".join(parts)

    # 범용: key: value 나열
    parts = [prefix] if prefix else []
    for k, v in list(vals.items())[:8]:
        parts.append(f"{k}: {v}")
    return " ".join(parts)


def _df_to_sentences(df: pd.DataFrame, title: str | None = None) -> str:
    """DataFrame을 행별 자연어 문장으로 변환한다."""
    cols = [str(c) for c in df.columns]
    lines = []
    for _, row in df.iterrows():
        sentence = _row_to_natural_sentence(row, cols, title)
        if sentence:
            lines.append(sentence)
    return "\n".join(lines)


def extract_csv_text(file_path: str) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            df = pd.read_csv(file_path, encoding=encoding)
            break
        except (UnicodeDecodeError, Exception):
            continue
    else:
        raise ValueError("CSV 파일 인코딩을 감지할 수 없습니다.")

    if df.empty:
        return ""

    if _is_service_catalog(df):
        return _service_catalog_to_text(df)

    # 일반 CSV: 행별 자연어 문장으로 변환 (마크다운 테이블보다 검색 정확도 높음)
    return _df_to_sentences(df)


# ── 파일 형식별 추출 ──────────────────────────────────────────────────────────

_MARKITDOWN_SUPPORTED = {
    ".pdf", ".docx", ".doc",
    ".pptx", ".ppt", ".html", ".htm", ".md",
}


def _extract_excel_text(file_path: str) -> str:
    """엑셀 파일을 시트별 자연어 문장으로 변환."""
    xl = pd.ExcelFile(file_path)
    parts = []
    for sheet_name in xl.sheet_names:
        df = xl.parse(sheet_name)
        if df.empty:
            continue
        if _is_service_catalog(df):
            text = _service_catalog_to_text(df)
        else:
            title = sheet_name if len(xl.sheet_names) > 1 else None
            text = _df_to_sentences(df, title=title)
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


_ROW_CHUNK_SIZE = 10  # 행 몇 개씩 하나의 chunk로 묶을지


def _rows_to_chunks(rows: list[str], chunk_size: int = _ROW_CHUNK_SIZE, header: str | None = None) -> list[str]:
    """행 문자열 리스트를 chunk_size개씩 묶어 chunk 리스트로 반환한다.
    header가 있으면 각 chunk 앞에 붙여 검색 정확도를 높인다."""
    chunks = []
    for i in range(0, len(rows), chunk_size):
        batch = rows[i:i + chunk_size]
        if not batch:
            continue
        text = "\n".join(batch)
        if header:
            text = f"{header}\n{text}"
        chunks.append(text)
    return chunks


def _df_to_row_sentences(df: pd.DataFrame) -> list[str]:
    cols = [str(c) for c in df.columns]
    rows = []
    for _, row in df.iterrows():
        parts = [f"{col}: {_clean_cell(row[col])}" for col in cols
                 if _clean_cell(row[col]) and _clean_cell(row[col]).lower() not in ("nan", "none", "-", "")]
        if parts:
            rows.append(", ".join(parts))
    return rows


def extract_chunks_from_file(file_path: str, file_name: str) -> list[str] | None:
    """
    행별 데이터(Excel/CSV)는 10행씩 묶은 chunk 리스트를 반환한다.
    행이 글자수 기준으로 잘리지 않아 데이터 무결성이 유지된다.
    일반 문서(PDF/Word 등)는 None을 반환해 기본 chunker가 처리하게 한다.
    """
    suffix = Path(file_name).suffix.lower()

    base_name = Path(file_name).stem  # 확장자 제외 파일명

    if suffix == ".csv":
        for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                break
            except Exception:
                continue
        else:
            return None
        if df.empty or _is_service_catalog(df):
            return None
        cols = " | ".join(str(c) for c in df.columns)
        header = f"[{base_name}] 컬럼: {cols}"
        rows = _df_to_row_sentences(df)
        return _rows_to_chunks(rows, header=header) or None

    # xlsx/xls는 _extract_excel_text(자연어 문장 변환 후 기본 chunker)로 처리
    return None


def extract_text_from_file(file_path: str, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        return _extract_excel_text(file_path)

    if suffix in {".txt"}:
        with open(file_path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read().strip()

    if suffix in _MARKITDOWN_SUPPORTED:
        result = _md.convert(file_path)
        return (result.text_content or "").strip()

    raise ValueError(f"Unsupported file type: {suffix}")


# ── URL 추출 ─────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def extract_text_from_url(url: str) -> str:
    response = httpx.get(url, timeout=15, follow_redirects=True, headers=_HEADERS)
    response.raise_for_status()
    text = trafilatura.extract(response.text) or ""
    return text.strip()
