"""Self-check: grid_window must keep newest (rightmost) date columns."""
import data_loader as dl


def test_prefers_width_over_rows():
    # Loss HJR-ish: wide sheet, inflated rowCount — old logic capped cols at 700
    # and then shrunk width further under budget.
    mr, mc = dl.grid_window(726, 1000)
    assert mc >= 726, (mr, mc)
    assert mr * mc <= 400_000


def test_raises_old_700_cap():
    mr, mc = dl.grid_window(800, 50)
    assert mc == 800, (mr, mc)


def test_budget_shrinks_rows_first():
    mr, mc = dl.grid_window(2000, 1000)
    assert mc == 2000, (mr, mc)
    assert mr == 200, (mr, mc)  # 400_000 // 2000


def test_narrow_unchanged():
    assert dl.grid_window(500, 100) == (100, 500)


if __name__ == "__main__":
    test_prefers_width_over_rows()
    test_raises_old_700_cap()
    test_budget_shrinks_rows_first()
    test_narrow_unchanged()
    print("ok")
