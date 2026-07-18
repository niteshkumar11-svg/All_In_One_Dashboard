"""
Data loading & parsing for the Ops KPI Dashboard.

The Google Sheet is NOT a single clean table. Each hub lives on its own
worksheet (or stacked block) shaped like:

    | Charter | Metrics | Target | 15-Jun-2026 | 14-Jun-2026 | ... |
    | People  | AOP     | 222    | 222         | 222         | ... |
    |         | Active  | 222    | 218         | 219         | ... |
    | Volume  | MP Load | ...                                       |
    ...

This module turns that wide, irregular layout into a single tidy
("long") DataFrame with one row per (Hub, Charter, Metric, Date):

    Hub | Charter | Metric | Target | Target_raw | Date | Value | Value_raw | IsPercent

Two data sources are supported:
  1. LIVE   -> Google Sheets API via a service account (gspread).
  2. SNAPSHOT-> a bundled CSV produced from a one-off export (offline / demo).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
SPREADSHEET_ID = "1OfU6KGYvY-mD1K-F5VC6oILkyvxEDvqTjSnIF7lCKFA"
SNAPSHOT_PATH = Path(__file__).parent / "data" / "snapshot.csv"

# A row starts a Charter block when its first cell is "Charter" and one of the
# next cells is "Target". The col right after is "Metrics" or "Metrics/Date".
_HEADER_FIRST = "charter"
_NON_NUMERIC = {"", "-", "na", "n/a", "dnf", "nan", "#div/0!", "#n/a"}

# Tidy column order. `Order` preserves the metric's position within the sheet
# so tables can be rendered in the exact order they appear in the Google Sheet.
# The *Bg columns hold the cell background colours (hex) read from the sheet so
# the dashboard can reproduce the sheet's conditional formatting.
TIDY_COLS = [
    "Hub", "Charter", "Metric", "Target", "Target_raw",
    "Date", "Value", "Value_raw", "IsPercent", "Order",
    "BgColor", "MetricBg", "TargetBg", "CharterBg",
    "FrozenRows", "FrozenCols",
]


# --------------------------------------------------------------------------- #
# Cleaning helpers
# --------------------------------------------------------------------------- #
def _clean_text(s: str) -> str:
    """Strip markdown escaping (\\-, \\*, &#9;) and surrounding whitespace."""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("&#9;", " ").replace("\\*", "*").replace("\\-", "-")
    s = s.replace("\\>", ">").replace("\\<", "<")
    return s.strip()


def clean_value(raw: str):
    """
    Return (numeric_value_or_None, is_percent).

    Handles: "96,649" -> 96649 ; "113.50%" -> 1.135 (is_percent=True) ;
    "-3" / "\\-3" -> -3 ; "6 (MH-6)" -> 6 ; "DNF"/"" -> None.
    """
    txt = _clean_text(raw)
    low = txt.lower()
    if low in _NON_NUMERIC:
        return None, False

    is_percent = txt.endswith("%")
    # pull the first number (optionally signed / decimal / comma-grouped)
    candidate = txt.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", candidate)
    if not m:
        return None, is_percent
    val = float(m.group())
    if is_percent:
        val = val / 100.0
    return val, is_percent


def _parse_date(s: str):
    """Parse '15-Jun-2026' / '1-April-2026' / '2-Apr-2026' -> Timestamp or NaT."""
    s = _clean_text(s)
    if not s:
        return pd.NaT
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            continue
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


# --------------------------------------------------------------------------- #
# Core block parser
# --------------------------------------------------------------------------- #
def _color_at(color_grid, r: int, c: int) -> str:
    """Safe lookup into an optional aligned colour grid."""
    if not color_grid or r >= len(color_grid):
        return ""
    row = color_grid[r]
    return row[c] if c < len(row) else ""


def parse_grid(grid: list[list[str]], hub_label: str,
               color_grid: list[list[str]] | None = None) -> pd.DataFrame:
    """
    Find every Charter/Metrics time-series block in a 2-D grid and melt them
    into tidy rows. `hub_label` tags the source (worksheet title in live mode).
    If a grid holds several blocks they are suffixed " #2", " #3", ...

    `color_grid` (optional) is a grid of hex colours aligned cell-for-cell with
    `grid`; when supplied, each record carries the source cell's background.
    """
    records: list[dict] = []
    n = len(grid)
    i = 0
    block_no = 0

    while i < n:
        row = [_clean_text(c) for c in grid[i]]
        first = row[0].lower() if row else ""

        is_header = (
            first == _HEADER_FIRST
            and any(c.lower() == "target" for c in row[1:4])
        )
        if not is_header:
            i += 1
            continue

        # locate the Target column and the date columns that follow it
        try:
            tgt_idx = next(j for j, c in enumerate(row) if c.lower() == "target")
        except StopIteration:
            i += 1
            continue

        date_cols = []  # (col_index, Timestamp)
        for j in range(tgt_idx + 1, len(row)):
            d = _parse_date(row[j])
            if pd.notna(d):
                date_cols.append((j, d))
        if not date_cols:
            i += 1
            continue

        block_no += 1
        this_hub = hub_label if block_no == 1 else f"{hub_label} #{block_no}"

        # skip header + separator (':-:' alignment row), then read data rows
        i += 1
        if i < n and all(set(_clean_text(c)) <= set(":-") for c in grid[i] if _clean_text(c)):
            i += 1

        current_charter = ""
        current_charter_bg = ""
        order = 0                                   # metric position within block
        while i < n:
            drow = [_clean_text(c) for c in grid[i]]
            if not any(drow):                       # blank row -> block ends
                break
            nxt = drow[0].lower() if drow else ""
            if nxt == _HEADER_FIRST:                # next block starts
                break

            charter = drow[0] if len(drow) > 0 else ""
            metric = drow[1] if len(drow) > 1 else ""
            if charter:
                current_charter = charter
                current_charter_bg = _color_at(color_grid, i, 0)
            if not metric:                          # nothing to record
                i += 1
                continue

            target_raw = drow[tgt_idx] if len(drow) > tgt_idx else ""
            target_val, _ = clean_value(target_raw)
            metric_bg = _color_at(color_grid, i, 1)
            target_bg = _color_at(color_grid, i, tgt_idx)

            for col_idx, dt in date_cols:
                raw = drow[col_idx] if len(drow) > col_idx else ""
                val, is_pct = clean_value(raw)
                records.append({
                    "Hub": this_hub,
                    "Charter": current_charter,
                    "Metric": metric,
                    "Target": target_val,
                    "Target_raw": _clean_text(target_raw),
                    "Date": dt,
                    "Value": val,
                    "Value_raw": _clean_text(raw),
                    "IsPercent": is_pct,
                    "Order": order,
                    "BgColor": _color_at(color_grid, i, col_idx),
                    "MetricBg": metric_bg,
                    "TargetBg": target_bg,
                    "CharterBg": current_charter_bg,
                })
            order += 1
            i += 1

    if not records:
        return pd.DataFrame(columns=TIDY_COLS)
    df = pd.DataFrame.from_records(records)
    # sensible defaults; live mode overrides per worksheet
    df["FrozenRows"] = 1
    df["FrozenCols"] = 3
    df = df[TIDY_COLS]
    # A metric is "percent" if any of its cells were percentages.
    pct_metrics = df.loc[df["IsPercent"], "Metric"].unique()
    df["IsPercent"] = df["Metric"].isin(pct_metrics)
    return df


# --------------------------------------------------------------------------- #
# Live source (Google Sheets API)
# --------------------------------------------------------------------------- #
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_API = "https://sheets.googleapis.com/v4/spreadsheets"
_MAX_ROWS = 120          # charter tables sit at the top; bounds the payload
_MAX_COL = "AZ"          # 52 cols — covers Target + plenty of daily columns


def _bg_hex(cell: dict | None) -> str:
    """effectiveFormat.backgroundColor -> '#rrggbb'. White / none -> ''."""
    if not cell:
        return ""
    bg = cell.get("effectiveFormat", {}).get("backgroundColor")
    if not bg:
        return ""
    h = "#%02x%02x%02x" % (
        round(bg.get("red", 0.0) * 255),
        round(bg.get("green", 0.0) * 255),
        round(bg.get("blue", 0.0) * 255),
    )
    return "" if h == "#ffffff" else h


def load_live(
    service_account_info: dict,
    spreadsheet_id: str = SPREADSHEET_ID,
    include_hidden: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    Pull every *visible* worksheet (values + cell background colours) via the
    Google Sheets REST API. Returns ``(tidy_df, raw_sheets)`` where:
      • tidy_df    – melted Charter/Metrics tables (the hub dashboards).
      • raw_sheets – {title: {"values", "colors", "frozen"}} for visible tabs
                     that are NOT Charter tables (e.g. BRSNR, Arkham Pendency).
    Hidden tabs are skipped unless `include_hidden=True`.
    """
    from urllib.parse import quote
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession

    creds = Credentials.from_service_account_info(service_account_info, scopes=_SCOPES)
    sess = AuthorizedSession(creds)

    # 1) which tabs are visible? + their freeze settings + merged ranges
    meta = sess.get(
        f"{_API}/{spreadsheet_id}",
        params={"fields": "sheets(properties(title,hidden,"
                          "gridProperties(frozenRowCount,frozenColumnCount)),merges)"},
    ).json()
    if "sheets" not in meta:
        raise ValueError(f"Sheets API error: {meta.get('error', meta)}")
    titles, frozen, merges_map = [], {}, {}
    for s in meta["sheets"]:
        p = s["properties"]
        if include_hidden or not p.get("hidden", False):
            titles.append(p["title"])
            gp = p.get("gridProperties", {})
            frozen[p["title"]] = (gp.get("frozenRowCount", 1),
                                  gp.get("frozenColumnCount", 3))
            merges_map[p["title"]] = [
                (m.get("startRowIndex", 0), m.get("endRowIndex", 0),
                 m.get("startColumnIndex", 0), m.get("endColumnIndex", 0))
                for m in s.get("merges", [])
            ]

    # 2) fetch values + colours for each visible tab (one bounded range each)
    frames, raw_sheets = [], {}
    for title in titles:
        rng = quote(f"'{title}'!A1:{_MAX_COL}{_MAX_ROWS}")
        url = (
            f"{_API}/{spreadsheet_id}?ranges={rng}&includeGridData=true"
            "&fields=sheets(data(rowData(values("
            "formattedValue,effectiveFormat.backgroundColor))))"
        )
        data = sess.get(url).json().get("sheets", [{}])[0].get("data", [{}])[0]
        row_data = data.get("rowData", [])

        value_grid, color_grid = [], []
        for rd in row_data:
            cells = rd.get("values", [])
            value_grid.append([c.get("formattedValue", "") for c in cells])
            color_grid.append([_bg_hex(c) for c in cells])
        if not value_grid:
            continue

        f = parse_grid(value_grid, hub_label=title, color_grid=color_grid)
        if not f.empty:
            fr, fc = frozen.get(title, (1, 3))
            f["FrozenRows"], f["FrozenCols"] = fr, fc
            frames.append(f)
        elif any(any(str(c).strip() for c in row) for row in value_grid):
            # visible, has data, but not a Charter table -> keep as a raw sheet
            raw_sheets[title] = {
                "values": value_grid,
                "colors": color_grid,
                "frozen": frozen.get(title, (1, 1)),
                "merges": merges_map.get(title, []),
            }

    if not frames and not raw_sheets:
        raise ValueError(
            "No data found in the visible worksheets. Check that the service "
            "account has access and the layout is intact."
        )
    tidy = (pd.concat(frames, ignore_index=True)
            if frames else pd.DataFrame(columns=TIDY_COLS))
    return tidy, raw_sheets


# --------------------------------------------------------------------------- #
# Snapshot source (bundled CSV)
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Tab-oriented loaders (BJOC sheet: each tab is a metric tagged "<Metric>-<Func>")
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Date parsing & per-metric format disambiguation (shared by app + tests)
# --------------------------------------------------------------------------- #
# Numeric dates are ambiguous (DD-MM vs MM-DD). We pick a day-first / month-first
# ORDER per metric (separator-agnostic) from the actual values, rather than a
# fixed global order. Groups are tried for ties in this PRIORITY: unambiguous
# named & ISO first, then day-first (DD-MM, the India/Flipkart default) before
# month-first (MM-DD) — so a genuinely ambiguous all-(<=12) column reads DD-MM,
# while month data containing any day>12 still forces MM-DD via the count score.
# Named-month forms (incl. YEAR-LESS like "21-May" / "08-Feb") are unambiguous on
# day vs month. Year-less dates get the current year assigned (see try_fmt).
_NAMED = ("%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%y",
          "%d-%b", "%d-%B", "%d %b", "%d %B", "%b-%d", "%b %d")
_YMD = ("%Y-%m-%d", "%Y/%m/%d")
_DMY = ("%d-%m-%Y", "%d/%m/%Y")           # day-first (India default on ties)
_MDY = ("%m-%d-%Y", "%m/%d/%Y")           # month-first (US)
_ORDER_GROUPS = (("named", _NAMED), ("ymd", _YMD), ("dmy", _DMY), ("mdy", _MDY))
DATE_FMTS = _NAMED + _YMD + _DMY + _MDY    # permissive order (day-first before month-first)


def _default_year():
    from datetime import date
    return date.today().year


def try_fmt(s, fmt):
    try:
        d = pd.to_datetime(str(s).strip(), format=fmt)
        if pd.isna(d):
            return None
        # year-less formats parse to year 1900 — assign the current year
        if "%Y" not in fmt and "%y" not in fmt:
            try:
                d = d.replace(year=_default_year())
            except ValueError:        # e.g. 29-Feb in a non-leap year
                return None
        return d
    except (ValueError, TypeError):
        return None


def _parse_group(s, fmts):
    for fmt in fmts:
        d = try_fmt(s, fmt)
        if d is not None:
            return d
    return None


def to_date(s):
    """Permissive parse — matches under ANY known format (day-first preferred).
    Used to LOCATE date-like cells; final values are re-parsed with the chosen
    per-metric order."""
    s = str(s).strip()
    if not s or len(s) < 5:        # allows year-less single-digit day, e.g. "8-Feb"
        return None
    for fmt in DATE_FMTS:
        d = try_fmt(s, fmt)
        if d is not None:
            return d
    return None


def _score_group(strings, fmts):
    """(#cells parsed, best monotonic fraction in EITHER direction). Real tabs
    can be laid out oldest- or newest-first, so monotonicity is bidirectional."""
    seq = [d for d in (_parse_group(s, fmts) for s in strings) if d is not None]
    if not seq:
        return (0, 0.0)
    if len(seq) == 1:
        return (1, 1.0)
    asc = sum(1 for a, b in zip(seq, seq[1:]) if b >= a)
    desc = sum(1 for a, b in zip(seq, seq[1:]) if b <= a)
    return (len(seq), max(asc, desc) / (len(seq) - 1))


def best_fmt(strings):
    """Diagnostic: the primary format of the best-fitting order group."""
    grp = _best_group(strings)
    return grp[0] if grp else None


def _best_group(strings):
    strings = [s for s in strings if str(s).strip()]
    best, best_score = None, (-1, -1.0)
    for _name, fmts in _ORDER_GROUPS:          # priority order; '>' keeps earlier on tie
        score = _score_group(strings, fmts)
        if score > best_score:
            best_score, best = score, fmts
    return best if best_score[0] > 0 else None


def choose_parser(strings):
    """Return a parser locked to the best day/month ORDER for these values
    (separator-agnostic), with a permissive fallback for stray cells in another
    format (e.g. a lone named-month date in an otherwise MM-DD column)."""
    grp = _best_group(strings)
    if not grp:
        return to_date

    def parse(s):
        s2 = str(s).strip()
        if not s2 or len(s2) < 5:        # allow year-less single-digit days, e.g. "1-Jul"
            return None
        d = _parse_group(s2, grp)
        return d if d is not None else to_date(s2)
    return parse


def _col_a1(n: int) -> str:
    """1-indexed column number -> spreadsheet letters (1->A, 27->AA)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_function(title: str) -> tuple[str, str]:
    """'Open STN-FC' -> ('Open STN', 'FC'); function = text after the last '-'."""
    if "-" in title:
        metric, func = title.rsplit("-", 1)
        return metric.strip(), func.strip().upper()
    return title.strip(), ""


def _session(service_account_info: dict):
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    creds = Credentials.from_service_account_info(service_account_info, scopes=_SCOPES)
    return AuthorizedSession(creds)


def load_meta(service_account_info: dict, spreadsheet_id: str = SPREADSHEET_ID,
              include_hidden: bool = False) -> list[dict]:
    """List visible tabs with parsed function/metric, freeze settings and merges.
    Cheap: one metadata call, no cell data."""
    sess = _session(service_account_info)
    meta = sess.get(
        f"{_API}/{spreadsheet_id}",
        params={"fields": "sheets(properties(title,hidden,gridProperties("
                          "rowCount,columnCount,frozenRowCount,frozenColumnCount)),merges)"},
    ).json()
    if "sheets" not in meta:
        raise ValueError(f"Sheets API error: {meta.get('error', meta)}")
    tabs = []
    for s in meta["sheets"]:
        p = s["properties"]
        if not include_hidden and p.get("hidden", False):
            continue
        gp = p.get("gridProperties", {})
        metric, func = parse_function(p["title"])
        tabs.append({
            "title": p["title"], "metric": metric, "function": func,
            "rows": gp.get("rowCount", 0), "cols": gp.get("columnCount", 0),
            # honour the sheet's own freeze; default to NONE (don't freeze by default)
            "frozen": (gp.get("frozenRowCount", 0), gp.get("frozenColumnCount", 0)),
            "merges": [
                (m.get("startRowIndex", 0), m.get("endRowIndex", 0),
                 m.get("startColumnIndex", 0), m.get("endColumnIndex", 0))
                for m in s.get("merges", [])
            ],
        })
    return tabs


def load_tab_grid(service_account_info: dict, title: str,
                  spreadsheet_id: str = SPREADSHEET_ID,
                  max_rows: int = 300, max_cols: int = 200) -> tuple[list, list, bool]:
    """Fetch one tab's values + per-cell formatting (bounded window).
    Returns (values_grid, format_grid, truncated) where each format cell is a
    (background_hex, is_bold) tuple mirroring the sheet's own formatting."""
    from urllib.parse import quote
    sess = _session(service_account_info)
    safe = title.replace("'", "''")
    rng = quote(f"'{safe}'!A1:{_col_a1(max_cols)}{max_rows}")
    url = (
        f"{_API}/{spreadsheet_id}?ranges={rng}&includeGridData=true"
        "&fields=sheets(data(rowData(values("
        "formattedValue,effectiveFormat.backgroundColor,effectiveFormat.textFormat.bold))))"
    )
    data = sess.get(url).json().get("sheets", [{}])[0].get("data", [{}])[0]
    row_data = data.get("rowData", [])
    values, colors = [], []
    for rd in row_data:
        cells = rd.get("values", [])
        values.append([c.get("formattedValue", "") for c in cells])
        colors.append([(_bg_hex(c),
                        bool(c.get("effectiveFormat", {}).get("textFormat", {}).get("bold")))
                       for c in cells])
    truncated = len(values) >= max_rows or any(len(r) >= max_cols for r in values)
    return values, colors, truncated


def load_snapshot(path: Path = SNAPSHOT_PATH) -> tuple[pd.DataFrame, dict]:
    """Returns ``(tidy_df, {})`` — the bundled CSV holds no raw aux sheets."""
    if not Path(path).exists():
        raise FileNotFoundError(f"Snapshot not found at {path}")
    df = pd.read_csv(path, parse_dates=["Date"])
    for col in TIDY_COLS:
        if col not in df.columns:
            df[col] = pd.NA
    # colour columns must be strings ('' = no fill) for the renderer
    for col in ("BgColor", "MetricBg", "TargetBg", "CharterBg"):
        df[col] = df[col].fillna("").astype(str)
    df["FrozenRows"] = df["FrozenRows"].fillna(1).astype(int)
    df["FrozenCols"] = df["FrozenCols"].fillna(3).astype(int)
    return df[TIDY_COLS], {}
