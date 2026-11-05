import data_loader as dl


def test_effective_sheet_columns_prefers_probe():
    old = dl.probe_used_columns
    try:
        dl.probe_used_columns = lambda *a, **k: 1850
        assert dl.effective_sheet_columns({}, "X", 900) == 1850
        assert dl.effective_sheet_columns({}, "X", 2000) == 2000
    finally:
        dl.probe_used_columns = old


if __name__ == "__main__":
    test_effective_sheet_columns_prefers_probe()
    print("ok")
