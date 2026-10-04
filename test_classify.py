"""Tests for complete/classify_grantmakers.py pure helpers (no DB, no API)."""

from complete.classify_grantmakers import build_prompt, parse_verdict


def test_build_prompt_contains_identity_and_json_contract():
    p = build_prompt({"name": "Currie Community Church", "charity_number": "SC000113",
                      "source_register": "scotland", "description": "Weekly worship"})
    assert "Currie Community Church" in p and "SC000113" in p
    assert "is_grantmaker" in p and "Weekly worship" in p


def test_parse_verdict_clean_json():
    v = parse_verdict('{"is_grantmaker": true, "confidence": 0.9, "reason": "gives grants yearly"}')
    assert v == {"is_grantmaker": True, "confidence": 0.9, "reason": "gives grants yearly"}


def test_parse_verdict_prose_wrapped():
    v = parse_verdict('Analysis done.\n{"is_grantmaker": false, "confidence": 0.8, "reason": "parish church"} bye')
    assert v["is_grantmaker"] is False and v["confidence"] == 0.8


def test_parse_verdict_garbage():
    assert parse_verdict("no json here")["reason"] == "unparseable"
