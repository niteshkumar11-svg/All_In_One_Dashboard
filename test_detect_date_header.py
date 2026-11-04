"""Regression: pick the active date header row, not a wide empty legacy row."""
import pandas as pd

import data_loader as dl


def test_prefers_row_with_newest_dates_and_data():
    """Row 0 is a wide legacy header (empty); row 1 is the live 4-day header."""
    nleg = 10
    legacy = [f"10/{20 + i}/2026" for i in range(nleg)]
    live = ["11/1/2026", "11/2/2026", "11/3/2026", "11/4/2026"]
    grid = [
        ["Day"] + legacy,
        ["Day"] + live,
        [">30 Days"] + ["-"] * nleg,
        [">30 Days"] + ["12", "11", "10", "9"],
        ["Total"] + ["0"] * nleg,
        ["Total"] + ["18", "18", "16", "14"],
    ]
    row, starts = dl.detect_date_header(grid)
    assert row == 1, row
    dates = sorted({d.date() for _, d in starts}, reverse=True)
    assert dates == [
        pd.Timestamp("2026-11-04").date(),
        pd.Timestamp("2026-11-03").date(),
        pd.Timestamp("2026-11-02").date(),
        pd.Timestamp("2026-11-01").date(),
    ]


def test_excel_serial_date_in_header():
    serial = str((pd.Timestamp("2026-11-04") - pd.Timestamp("1899-12-30")).days)
    assert dl.to_date(serial) == pd.Timestamp("2026-11-04")


def test_ambiguous_slash_dates_use_daily_cadence():
    row = ["11/1/2026", "11/2/2026", "11/3/2026", "11/4/2026"]
    parser = dl.choose_parser(row)
    got = [parser(x).month for x in row]
    assert got == [11, 11, 11, 11]


if __name__ == "__main__":
    test_prefers_row_with_newest_dates_and_data()
    test_excel_serial_date_in_header()
    test_ambiguous_slash_dates_use_daily_cadence()
    print("ok")
