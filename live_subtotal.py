"""Recompute a Subtotal/Total row from the currently visible data rows."""


def parse_num(s):
    """Parse a cell to float; returns (value, is_percent) or None."""
    s = str(s).strip()
    if not s:
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1]), True
        return float(s.replace(",", "")), False
    except (ValueError, TypeError):
        return None


def find_subtotal_row(grid, max_row=None):
    """Index of a Subtotal/Total label in column 0, or None."""
    limit = len(grid) if max_row is None else min(max_row + 1, len(grid))
    for r in range(limit):
        first = str(grid[r][0]).strip().lower() if grid[r] else ""
        if first.startswith("subtotal") or first.startswith("grand total") or first == "total":
            return r
    return None


def _fmt_num(val, as_pct=False):
    """Format an aggregated number; whole numbers stay integer-looking."""
    if as_pct:
        return f"{val:.2f}%"
    return f"{val:,.0f}" if abs(val - round(val)) < 1e-9 else f"{val:,.2f}"


def live_subtotal(values, subtotal_row, visible_rows, all_data_rows=None):
    """Return a copy of `values` with `subtotal_row` recomputed from `visible_rows`.

    Aggregation matches the sheet when possible: for each numeric column, compare the
    sheet's existing subtotal to the sum vs the mean of *all* data rows and reuse that
    mode on the currently visible rows. Percent columns always average. Empty visible
    sets clear the cell (no stale full-range total).
    """
    if subtotal_row is None or not values:
        return values
    ref_rows = all_data_rows if all_data_rows is not None else visible_rows
    wv = [list(r) for r in values]
    ncols = max((len(r) for r in values), default=0)
    while len(wv[subtotal_row]) < ncols:
        wv[subtotal_row].append("")

    def col_nums(rows, c):
        out, pct_n = [], 0
        for r in rows:
            parsed = parse_num(values[r][c] if c < len(values[r]) else "")
            if parsed is None:
                continue
            n, is_pct = parsed
            out.append(n)
            if is_pct:
                pct_n += 1
        return out, (bool(out) and pct_n >= len(out) * 0.5)

    for c in range(1, ncols):
        ref_nums, is_pct = col_nums(ref_rows, c)
        vis_nums, vis_pct = col_nums(visible_rows, c)
        if not ref_nums and not vis_nums:
            continue                       # text/label column — leave as-is
        is_pct = is_pct or vis_pct
        if not vis_nums:
            wv[subtotal_row][c] = ""
            continue
        if is_pct:
            wv[subtotal_row][c] = _fmt_num(sum(vis_nums) / len(vis_nums), as_pct=True)
            continue
        # Infer sum vs average from the sheet's own subtotal vs the full data.
        mode = "avg"
        sheet_raw = str(values[subtotal_row][c]).strip() if c < len(values[subtotal_row]) else ""
        sheet_parsed = parse_num(sheet_raw)
        if sheet_parsed is not None and ref_nums:
            sheet_n = sheet_parsed[0]
            s, a = sum(ref_nums), sum(ref_nums) / len(ref_nums)
            mode = "sum" if abs(sheet_n - s) + 1e-6 < abs(sheet_n - a) else "avg"
        val = sum(vis_nums) if mode == "sum" else sum(vis_nums) / len(vis_nums)
        wv[subtotal_row][c] = _fmt_num(val)
    return wv


if __name__ == "__main__":
    # Sheet subtotal (~28) matches the average of all days, not the sum.
    g = [
        ["Target", "35", "35"],
        ["Subtotal", "28", "30"],
        ["Date", "Hub_A", "Hub_B"],
        ["2026-07-06", "30", "32"],
        ["2026-07-07", "23", "28"],
        ["2026-07-08", "25", "30"],
        ["2026-07-09", "24", "29"],
        ["2026-07-10", "26", "31"],
        ["2026-07-11", "28", "30"],
        ["2026-07-12", "29", "30"],
    ]
    days = list(range(3, 10))
    out = live_subtotal(g, 1, days, all_data_rows=days)
    assert out[1][1] == "26.43" and out[1][2] == "30", out[1]
    out4 = live_subtotal(g, 1, [6, 7, 8, 9], all_data_rows=days)
    assert out4[1][1] == "26.75", out4[1]
    g2 = [["Subtotal", "100", "10%"], ["d1", "40", "8%"], ["d2", "60", "12%"]]
    out2 = live_subtotal(g2, 0, [1], all_data_rows=[1, 2])
    assert out2[0][1] == "40" and out2[0][2] == "8.00%", out2[0]
    assert find_subtotal_row(g, max_row=2) == 1
    print("ok")
