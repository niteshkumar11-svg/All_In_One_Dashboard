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


def _from_serial(s: str):
    """Google Sheets / Excel day serial (origin 1899-12-30), when shown as a plain number."""
    try:
        n = float(str(s).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None
    if n != int(n):
        return None
    n = int(n)
    if n < 30000 or n > 70000:
        return None
    return pd.Timestamp("1899-12-30") + pd.Timedelta(days=n)


def to_date(s):
    """Permissive parse — matches under ANY known format (day-first preferred).
    Used to LOCATE date-like cells; final values are re-parsed with the chosen
    per-metric order."""
    s = str(s).strip()
    if not s:
        return None
    d = _from_serial(s)
    if d is not None:
        return d
    if len(s) < 5:        # allows year-less single-digit day, e.g. "8-Feb"
        return None
    for fmt in DATE_FMTS:
        d = try_fmt(s, fmt)
        if d is not None:
            return d
    return None


def detect_date_header(grid, max_search: int = 20, min_dates: int = 4):
    """Row index and [(col, Timestamp), ...] for the active date header.

    Picks the row whose dates extend furthest into the future and that has
    values under those columns — not a legacy template row full of empty
    placeholder dates (common on Invoice Sanity–style tabs).
    """
    best_row, best_starts = None, []
    best_key = None
    empty = {"", "-", "—", "na", "n/a"}

    for r in range(min(max_search, len(grid))):
        cols = [c for c in range(len(grid[r])) if to_date(grid[r][c]) is not None]
        if len(cols) < min_dates:
            continue
        parser = choose_parser([grid[r][c] for c in cols])
        starts = []
        for c in cols:
            d = parser(grid[r][c])
            if d is not None:
                starts.append((c, pd.Timestamp(d)))
        if len(starts) < min_dates:
            continue

        max_d = max(d for _, d in starts)
        fill = 0
        data_end = len(grid)
        for r_h in range(r + 1, min(len(grid), r + max_search)):
            n_h = sum(1 for c in range(len(grid[r_h])) if to_date(grid[r_h][c]) is not None)
            if n_h >= min_dates:
                data_end = r_h
                break
        for r2 in range(r + 1, data_end):
            row = grid[r2]
            for c, _ in starts:
                if c < len(row) and str(row[c]).strip() not in empty:
                    fill += 1

        key = (max_d, fill, len(starts))
        if best_key is None or key > best_key:
            best_key, best_row, best_starts = key, r, starts

    return best_row, sorted(best_starts, key=lambda x: x[0])


def _score_group(strings, fmts):
    """(#cells parsed, best monotonic fraction, daily-cadence tie-break).

    Real tabs can be oldest- or newest-first. When DD-MM and MM-DD both parse an
    ambiguous slash column (e.g. 11/1/2026), prefer the interpretation whose
    consecutive columns are ~1 day apart (Invoice Sanity, Bulk MM-DD, etc.).
    """
    seq = [d for d in (_parse_group(s, fmts) for s in strings) if d is not None]
    if not seq:
        return (0, 0.0, -9999.0)
    if len(seq) == 1:
        return (1, 1.0, 0.0)
    asc = sum(1 for a, b in zip(seq, seq[1:]) if b >= a)
    desc = sum(1 for a, b in zip(seq, seq[1:]) if b <= a)
    mono = max(asc, desc) / (len(seq) - 1)
    deltas = [abs(int((seq[i + 1] - seq[i]).days)) for i in range(len(seq) - 1)]
    cadence = -(sum(deltas) / len(deltas))   # higher score when deltas are smaller
    return (len(seq), mono, cadence)


def best_fmt(strings):
    """Diagnostic: the primary format of the best-fitting order group."""
    grp = _best_group(strings)
    return grp[0] if grp else None


def _best_group(strings):
    strings = [s for s in strings if str(s).strip()]
    best, best_score = None, (-1, -1.0, -9999.0)
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
        if not s2:
            return None
        d = _from_serial(s2)
        if d is not None:
            return d
        if len(s2) < 5:        # allow year-less single-digit days, e.g. "1-Jul"
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


def grid_window(ncols: int, nrows: int,
                col_cap: int = 2500, row_cap: int = 400,
                budget: int = 800_000) -> tuple[int, int]:
    """API fetch window (rows, cols). Prefer width: newest dates are rightmost.

    Wide FC tabs (HJR, Herbalife, …) extend past column 2000 by Nov; a single
    A1 window capped at 2000 columns silently drops those days and Overall
    keeps showing stale July dates. Chunks in load_tab_grid use this width.
    """
    mc = min(max(int(ncols), 60), col_cap)
    mr = min(max(int(nrows), 40), row_cap)
    if mr * mc > budget:
        mr = max(40, budget // mc)          # shrink rows first
        if mr * mc > budget:
            mc = max(60, budget // mr)
    return mr, mc


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


def probe_used_columns(service_account_info: dict, title: str,
                       spreadsheet_id: str = SPREADSHEET_ID,
                       header_rows: int = 100) -> int:
    """Rightmost populated column in the header area (Values API, trailing-empty trim).

    gridProperties.columnCount often lags when new date columns are added at the
    far right; trusting it alone left HJR / Herbalife stuck on July dates.
    """
    from urllib.parse import quote
    sess = _session(service_account_info)
    safe = title.replace("'", "''")
    rng = quote(f"'{safe}'!1:{header_rows}")
    url = f"{_API}/{spreadsheet_id}/values/{rng}"
    resp = sess.get(url).json()
    if "error" in resp:
        return 0
    rows = resp.get("values") or []
    return max((len(r) for r in rows), default=0)


def effective_sheet_columns(service_account_info: dict, title: str,
                            meta_cols: int,
                            spreadsheet_id: str = SPREADSHEET_ID) -> int:
    probed = probe_used_columns(service_account_info, title, spreadsheet_id)
    return max(int(meta_cols or 0), probed, 60)


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


def _grid_from_row_data(row_data: list) -> tuple[list, list]:
    values, colors = [], []
    for rd in row_data:
        cells = rd.get("values", [])
        values.append([c.get("formattedValue", "") for c in cells])
        colors.append([(_bg_hex(c),
                        bool(c.get("effectiveFormat", {}).get("textFormat", {}).get("bold")))
                       for c in cells])
    return values, colors


def _merge_grid_chunk(grid_v, grid_c, col_offset: int,
                      chunk_v, chunk_c, total_cols: int):
    if not chunk_v:
        return grid_v, grid_c
    if grid_v is None:
        nrows = len(chunk_v)
        grid_v = [[""] * total_cols for _ in range(nrows)]
        grid_c = [[("", False)] * total_cols for _ in range(nrows)]
    if len(chunk_v) > len(grid_v):
        extra = len(chunk_v) - len(grid_v)
        grid_v.extend([[""] * total_cols for _ in range(extra)])
        grid_c.extend([[("", False)] * total_cols for _ in range(extra)])
    for r, row in enumerate(chunk_v):
        for j, val in enumerate(row):
            ci = col_offset + j
            if ci < total_cols:
                grid_v[r][ci] = val
                grid_c[r][ci] = chunk_c[r][j]
    return grid_v, grid_c


def load_tab_grid(service_account_info: dict, title: str,
                  spreadsheet_id: str = SPREADSHEET_ID,
                  max_rows: int = 300, max_cols: int = 200,
                  total_cols: int | None = None) -> tuple[list, list, bool]:
    """Fetch one tab's values + per-cell formatting (bounded window).

    When the sheet is wider than ``max_cols``, fetches column chunks and merges
    so rightmost date columns (e.g. Nov) are not dropped. Returns
    (values_grid, format_grid, truncated).
    """
    from urllib.parse import quote
    sess = _session(service_account_info)
    safe = title.replace("'", "''")
    meta_cols = int(total_cols or max_cols)
    sheet_cols = effective_sheet_columns(service_account_info, title, meta_cols,
                                         spreadsheet_id)
    chunk_w = max_cols
    fields = ("sheets(data(rowData(values(formattedValue,effectiveFormat.backgroundColor,"
              "effectiveFormat.textFormat.bold))))")

    def fetch_range(c0: int, c1: int):
        rng = quote(f"'{safe}'!{_col_a1(c0)}1:{_col_a1(c1)}{max_rows}")
        url = f"{_API}/{spreadsheet_id}?ranges={rng}&includeGridData=true&fields={fields}"
        data = sess.get(url).json()
        if "error" in data:
            raise ValueError(f"Sheets API error loading {title!r}: {data['error']}")
        return _grid_from_row_data(data.get("sheets", [{}])[0].get("data", [{}])[0]
                                   .get("rowData", []))

    grid_v, grid_c = None, None
    width_trunc = False
    if sheet_cols <= chunk_w:
        grid_v, grid_c = fetch_range(1, sheet_cols)
        width_trunc = any(len(r) >= sheet_cols for r in grid_v)
    else:
        for c0 in range(1, sheet_cols + 1, chunk_w):
            c1 = min(c0 + chunk_w - 1, sheet_cols)
            chunk_v, chunk_c = fetch_range(c0, c1)
            width_trunc = width_trunc or any(len(r) >= (c1 - c0 + 1) for r in chunk_v)
            grid_v, grid_c = _merge_grid_chunk(grid_v, grid_c, c0 - 1, chunk_v, chunk_c,
                                               sheet_cols)
    truncated = len(grid_v or []) >= max_rows or width_trunc
    return grid_v or [], grid_c or [], truncated


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
