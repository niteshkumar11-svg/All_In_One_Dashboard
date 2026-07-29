"""
Regression / verification tool for date detection across every metric tab.

Uses the SAME date logic the dashboard uses (data_loader.to_date / choose_parser)
so there is no drift. For each visible tab it reports the detected date layout,
the chosen format, the latest 4 dates (what the Overall tab shows), and the
months present (what the Month tab lists) — plus flags obvious anomalies
(e.g. a "latest" date in the far future, or no dates where the header looks dated).

Run:   python verify_dates.py            # all tabs
       python verify_dates.py "Bulk-FC"  # one tab
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pandas as pd

import data_loader as dl

SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"
TODAY = None  # set from args if needed; anomalies use a generous horizon instead


def _sa():
    return tomllib.load(open(SECRETS, "rb"))["gcp_service_account"]


def _drop_blank(values):
    if not values:
        return values
    nc = max(len(r) for r in values)
    keep = [c for c in range(nc) if any(c < len(r) and str(r[c]).strip() for r in values)]
    return [[(r[c] if c < len(r) else "") for c in keep] for r in values]


def _detect_header(grid, ms=6):
    best, br = [], None
    for r in range(min(ms, len(grid))):
        cols = [c for c in range(len(grid[r])) if dl.to_date(grid[r][c]) is not None]
        if len(cols) > len(best):
            best, br = cols, r
    return br, sorted(best)


def analyse(sa, tab):
    mr, mc = dl.grid_window(tab["cols"], tab["rows"])
    values, _colors, trunc = dl.load_tab_grid(sa, tab["title"], max_rows=mr, max_cols=mc)
    values = _drop_blank(values)
    if not values:
        return {"title": tab["title"], "layout": "empty", "dates": 0}

    # horizontal
    br, cols = _detect_header(values)
    layout, ds = "none", []
    if br is not None and len(cols) >= 4:
        parse = dl.choose_parser([values[br][c] for c in cols])
        ds = [(c, parse(values[br][c])) for c in cols if parse(values[br][c]) is not None]
        if len(ds) >= 4:
            layout = "horizontal"
    if layout == "none":
        # vertical (col 0)
        col0 = [(r, values[r][0]) for r in range(1, len(values))
                if values[r] and dl.to_date(values[r][0]) is not None]
        if len(col0) >= 4 and col0[0][0] <= 2:
            parse = dl.choose_parser([s for _, s in col0])
            ds = [(r, parse(s)) for r, s in col0 if parse(s) is not None]
            layout = "vertical"

    dates = sorted({d for _, d in ds})
    latest4 = [d.strftime("%d-%b-%Y") for d in sorted(dates, reverse=True)[:4]]
    months = sorted({(d.year, d.month) for d in dates})
    flags = []
    if dates:
        if dates[-1].year > 2027:
            flags.append(f"future date {dates[-1].date()}")
        if dates[-1] < pd.Timestamp("2026-06-01") and layout != "none":
            flags.append(f"latest only {dates[-1].date()}")
    return {
        "title": tab["title"], "func": tab["function"], "layout": layout,
        "n_dates": len(dates), "latest4": latest4,
        "months": [f"{y}-{m:02d}" for y, m in months],
        "truncated": trunc, "flags": flags,
    }


if __name__ == "__main__":
    sa = _sa()
    tabs = dl.load_meta(sa)
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for t in tabs:
        if only and t["title"] != only:
            continue
        r = analyse(sa, t)
        flag = ("  ⚠ " + "; ".join(r["flags"])) if r.get("flags") else ""
        print(f"{r['title']:24} [{r.get('layout',''):10}] dates={r.get('n_dates',0):4} "
              f"latest4={r.get('latest4',[])} months={r.get('months',[])}{flag}")
