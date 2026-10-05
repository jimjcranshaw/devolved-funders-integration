"""Tests for complete/extract_programmes.py pure helpers (no network/API/DB)."""

from complete.extract_programmes import build_extraction_prompt, map_to_row, parse_opportunities


def test_prompt_bans_invention_and_covers_both_gen_cols():
    p = build_extraction_prompt({"name": "The Gannochy Trust", "charity_number": "SC003133"}, "grants page")
    assert "never invent amounts or dates" in p
    assert "Gannochy" in p and "min_amount" in p and "funding_amounts" in p


def test_parse_clean_list():
    text = '{"opportunities": [{"opportunity_title": "Small Grants", "min_amount": 500}]}'
    assert len(parse_opportunities(text)) == 1


def test_parse_drops_untitled_and_garbage():
    assert parse_opportunities('{"opportunities": [{"description": "x"}]}') == []
    assert parse_opportunities("no json") == []


def test_map_to_row_types_and_source():
    row = map_to_row({"opportunity_title": "T", "min_amount": 100, "max_amount": "lots",
                      "excludes_individuals": 1, "funding_amounts": "£100+"}, 42)
    assert row["funder_id"] == 42 and row["opportunity_source"] == "devolved-crawl"
    assert row["min_amount"] == 100 and row["max_amount"] is None
    assert row["excludes_individuals"] is True and row["excludes_political"] is False
