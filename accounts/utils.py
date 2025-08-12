import csv
import io
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote
from urllib.request import urlopen
from urllib.error import URLError, HTTPError


def build_published_csv_url(sheet_id: str, sheet_name: str) -> str:
    """
    Build the public CSV export URL for a Google Sheet worksheet.

    This requires the sheet to be published to the web or shared publicly.
    """
    # Using the gviz CSV export endpoint supports selecting by sheet name
    # Ref: https://developers.google.com/chart/interactive/docs/spreadsheets
    encoded_sheet = quote(sheet_name, safe="")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_sheet}"


def build_export_csv_url_with_gid(sheet_id: str, gid: str) -> str:
    """
    Build the CSV export URL using a worksheet gid. This works even when we don't know the tab name.
    """
    encoded_gid = quote(gid, safe="")
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={encoded_gid}"


def fetch_sheet_as_rows(
    sheet_id: str,
    sheet_name: Optional[str] = None,
    gid: Optional[str] = None,
    timeout_sec: int = 10,
) -> Tuple[List[str], List[List[str]]]:
    """
    Fetch a Google Sheet (published CSV) and return header and data rows.

    Returns (headers, rows)
    """
    if gid:
        url = build_export_csv_url_with_gid(sheet_id, gid)
    else:
        if not sheet_name:
            raise Exception("Either sheet_name or gid must be provided")
        url = build_published_csv_url(sheet_id, sheet_name)
    try:
        with urlopen(url, timeout=timeout_sec) as resp:
            csv_bytes = resp.read()
    except HTTPError as e:
        raise Exception(f"HTTP error fetching sheet: {e.code} {e.reason}")
    except URLError as e:
        raise Exception(f"Network error fetching sheet: {e.reason}")
    csv_text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(csv_text))
    all_rows: List[List[str]] = list(reader)
    if not all_rows:
        return [], []
    headers = [h.strip() for h in all_rows[0]]
    data_rows = [row for row in all_rows[1:] if any(cell.strip() for cell in row)]
    # Normalize row lengths to headers length
    normalized_rows: List[List[str]] = [
        (row + [""] * (len(headers) - len(row)))[: len(headers)] for row in data_rows
    ]
    return headers, normalized_rows


def group_and_aggregate(
    headers: List[str],
    rows: List[List[str]],
    group_by_column: str,
    aggregate_column: str,
    agg: str = "sum",
) -> List[Dict[str, str]]:
    """
    Simple in-memory grouping and aggregation over CSV rows.

    agg can be one of: sum, count, avg, min, max.
    Returns list of {group: value, metric: aggregated_value}
    """
    if not headers:
        return []
    try:
        group_idx = headers.index(group_by_column)
        value_idx = headers.index(aggregate_column)
    except ValueError:
        return []

    groups: Dict[str, List[float]] = {}
    for row in rows:
        group_key = row[group_idx]
        raw_val = row[value_idx]
        parsed = _parse_float(raw_val)
        num_val = parsed if parsed is not None else 0.0
        groups.setdefault(group_key, []).append(num_val)

    results: List[Dict[str, str]] = []
    for key, values in groups.items():
        if not values:
            metric = 0.0
        elif agg == "count":
            metric = float(len(values))
        elif agg == "avg":
            metric = sum(values) / len(values)
        elif agg == "min":
            metric = min(values)
        elif agg == "max":
            metric = max(values)
        else:
            metric = sum(values)
        results.append({"group": key, "metric": metric})

    # Stable sort by group label
    results.sort(key=lambda x: (x["group"] is None, str(x["group"]).lower()))
    return results


def _parse_float(value: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Remove percent sign for inference
    if text.endswith("%"):
        text = text[:-1]
    # Remove commas
    text = text.replace(",", "")
    try:
        return float(text)
    except Exception:
        return None


def infer_default_columns(
    headers: List[str], rows: List[List[str]]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Infer a reasonable (group_by, agg_col) pair from data:
    - group_by: first non-numeric column with moderate cardinality
    - agg_col: numeric column with the highest ratio of numeric values
    """
    if not headers or not rows:
        return None, None

    num_cols = len(headers)
    column_values: List[List[str]] = [[] for _ in range(num_cols)]
    for row in rows[:1000]:
        for idx in range(min(num_cols, len(row))):
            column_values[idx].append(row[idx])

    numeric_ratio: List[float] = []
    for vals in column_values:
        total = len(vals) if vals else 1
        numeric_count = sum(1 for v in vals if _parse_float(v) is not None)
        numeric_ratio.append(numeric_count / total if total else 0.0)

    # Choose agg_col as column with highest numeric_ratio (>= 0.5)
    agg_col_idx = None
    best_ratio = 0.5
    for idx, ratio in enumerate(numeric_ratio):
        if ratio >= best_ratio:
            best_ratio = ratio
            agg_col_idx = idx

    # For group_by choose first non-numeric column with 2..50 distinct values
    group_by_idx = None
    for idx, ratio in enumerate(numeric_ratio):
        if ratio < 0.5:
            distinct = set(v.strip() for v in column_values[idx] if str(v).strip())
            if 2 <= len(distinct) <= 50:
                group_by_idx = idx
                break

    # Fallbacks
    if group_by_idx is None:
        group_by_idx = 0
    if agg_col_idx is None:
        agg_col_idx = 1 if len(headers) > 1 else 0

    return headers[group_by_idx], headers[agg_col_idx]


def find_best_metric_column(
    headers: List[str], rows: List[List[str]], preferred_names: Optional[List[str]] = None
) -> Optional[str]:
    """
    Choose a metric column name, preferring any in preferred_names if they are numeric-enough.
    Falls back to the most numeric column.
    """
    if not headers or not rows:
        return None
    name_to_idx = {h: i for i, h in enumerate(headers)}

    def is_numeric_enough(idx: int) -> float:
        total = 0
        numeric = 0
        for row in rows[:1000]:
            if idx < len(row):
                total += 1
                if _parse_float(row[idx]) is not None:
                    numeric += 1
        return (numeric / total) if total else 0.0

    if preferred_names:
        for name in preferred_names:
            if name in name_to_idx:
                if is_numeric_enough(name_to_idx[name]) >= 0.5:
                    return name

    # fallback: most numeric
    best_idx = None
    best_ratio = 0.5
    for idx, _ in enumerate(headers):
        ratio = is_numeric_enough(idx)
        if ratio >= best_ratio:
            best_ratio = ratio
            best_idx = idx
    return headers[best_idx] if best_idx is not None else None


def filter_rows_by_value(
    headers: List[str], rows: List[List[str]], column_name: str, value: str
) -> List[List[str]]:
    if not headers or not rows:
        return []
    try:
        col_idx = headers.index(column_name)
    except ValueError:
        return []
    return [r for r in rows if col_idx < len(r) and str(r[col_idx]).strip() == str(value).strip()]


