"""Tests for audit/gaps.py pure helpers (no DB)."""

from audit.gaps import pick_present, summarize


def test_pick_present_case_insensitive():
    assert pick_present(("deadlines", "deadline"), {"DEADLINES", "id"}) == ["deadlines"]


def test_summarize_counts():
    rows = [
        {"source_register": "scotland", "typical_value_band": "", "deadlines": ""},
        {"source_register": "scotland", "typical_value_band": "£5k-£10k", "deadlines": ""},
        {"source_register": "northern_ireland", "typical_value_band": None, "deadlines": None},
    ]
    s = summarize(rows, ["typical_value_band"], ["deadlines"])
    assert s["total_no_opp"] == 3
    assert s["by_register"]["scotland"] == {"no_opp_funders": 2, "missing_amount": 1, "missing_deadline": 2}
    assert s["by_register"]["northern_ireland"]["no_opp_funders"] == 1


def test_summarize_no_amount_cols():
    s = summarize([{"source_register": "scotland"}], [], [])
    assert s["by_register"]["scotland"] == {"no_opp_funders": 1, "missing_amount": 0, "missing_deadline": 0}
