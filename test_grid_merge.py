"""Merge wide-sheet column chunks (no API)."""
import data_loader as dl


def test_merge_second_chunk_places_values_at_offset():
    chunk_a = [["A", "1"], ["B", "2"]]
    ca = [[("", False), ("", False)]] * 2
    chunk_b = [["9", "10"]]
    cb = [[("", False), ("", False)]]

    gv, gc = dl._merge_grid_chunk(None, None, 0, chunk_a, ca, 4)
    gv, gc = dl._merge_grid_chunk(gv, gc, 2, chunk_b, cb, 4)

    assert gv[0] == ["A", "1", "9", "10"]
    assert gv[1][2] == ""


if __name__ == "__main__":
    test_merge_second_chunk_places_values_at_offset()
    print("ok")
