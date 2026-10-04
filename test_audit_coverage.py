"""Tests for audit/coverage.py (Issue #1). Run: python -m pytest -v"""

from audit.coverage import coverage_stats, dedupe_key, find_stale_registers


def test_dedupe_scottish_prefix():
    assert dedupe_key("SC123456", "Garfield Weston Foundation") == ("scotland", "SC123456")


def test_dedupe_ni_prefix():
    assert dedupe_key("NIC123456", "Some Trust") == ("northern_ireland", "NIC123456")


def test_dedupe_ew_digits():
    assert dedupe_key("1234567", "Some Trust") == ("england_wales", "1234567")


def test_dedupe_same_name_different_registers():
    scot = dedupe_key("SC000001", "Garfield Weston Foundation")
    ew = dedupe_key("1234567", "Garfield Weston Foundation")
    assert scot[0] != ew[0]  # name-only merge would collapse these


def test_coverage_basic():
    rows = [
        {"source_register": "scotland", "has_opportunity": True},
        {"source_register": "scotland", "has_opportunity": False},
        {"source_register": "northern_ireland", "has_opportunity": False},
    ]
    stats = coverage_stats(rows)
    assert stats["scotland"] == {"funders": 2, "with_opps": 1, "without_opps": 1, "pct_with_opps": 50.0}
    assert stats["northern_ireland"]["funders"] == 1


def test_coverage_empty():
    assert coverage_stats([]) == {}


def test_stale_detection():
    assert find_stale_registers({"scotland": 10, "northern_ireland": 200, "england_wales": 5}) == [
        "northern_ireland",
    ]


def test_stale_missing_register():
    assert "scotland" in find_stale_registers({})
