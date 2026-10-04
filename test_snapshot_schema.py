"""Tests for integrate/snapshot_schema.py pure helper (no DB)."""

from integrate.snapshot_schema import summarize_gaps


def test_gaps_all_present():
    snap = {
        "funders": ["id", "source_register", "oscr_purposes"],
        "funding_opportunities": ["id", "min_amount", "max_amount", "deadline"],
        "pages": ["id", "content"],
    }
    g = summarize_gaps(snap)
    assert g["funders_missing_source_register"] is False
    assert g["opp_amount_cols_present"] == ["max_amount", "min_amount"]
    assert g["opp_deadline_cols_present"] == ["deadline"]
    assert g["has_pages_table"] is True


def test_gaps_missing_everything():
    g = summarize_gaps({"funders": ["id", "name"]})
    assert g["funders_missing_source_register"] is True
    assert g["opp_amount_cols_present"] == []
    assert g["has_pages_table"] is False
