"""Tests for qa/run_checks.py pure helpers (no DB, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa.run_checks import (
    count_informative,
    count_populated,
    is_generic_title,
    is_placeholder,
    is_populated,
    is_substantive_beneficiary,
    pct,
    provenance_group,
    render_markdown,
)


def test_generic_exact_rubric_terms():
    for t in ("Main Grants", "GRANT PROGRAMME", "funding", "Grants", "Programme", ""):
        assert is_generic_title(t), t


def test_generic_general_prefix_boilerplate():
    assert is_generic_title("General Grantmaking - Assessment Needed")
    assert is_generic_title("general grant programme")
    assert not is_generic_title("NCCBC General Grant Funding")


def test_generic_funder_name_repeat():
    assert is_generic_title("Coats Foundation Trust", "coats foundation trust")
    assert not is_generic_title("Coats Emergency Fund", "Coats Foundation Trust")


def test_specific_title_passes():
    assert not is_generic_title("Young Start Fund", "National Lottery")


def test_populated_values():
    assert not is_populated(None)
    assert not is_populated("")
    assert not is_populated("   ")
    assert not is_populated([])
    assert is_populated("x")
    assert is_populated(0)  # numeric zero is data
    assert is_populated(False)  # explicit FALSE excludes_* is asserted data


def test_count_populated_subset():
    row = {"a": "x", "b": None, "c": "  ", "d": False}
    assert count_populated(row, ("a", "b", "c", "d")) == 2


def test_placeholder_needs_both_conditions():
    thin_generic = {"opportunity_title": "General Grant Programme", "funder_name": "X",
                    "description": "d"}
    assert is_placeholder(thin_generic)
    thin_specific = {"opportunity_title": "Young Start Fund", "funder_name": "X",
                     "description": "d"}
    assert not is_placeholder(thin_specific)
    rich_generic = {"opportunity_title": "General Grants", "funder_name": "X",
                    "description": "d", "funding_amounts": "a",
                    "deadlines": "dl", "eligibility_criteria": "e",
                    "beneficiary_groups": "b", "application_process": "p"}
    assert count_populated(rich_generic) == 7
    assert not is_placeholder(rich_generic)


def test_placeholder_boundary_is_five():
    row = {"opportunity_title": "Small Grants", "funder_name": "X",
           "description": "d", "funding_amounts": "a",
           "deadlines": "dl", "min_amount": 5}
    assert count_populated(row) == 5
    assert is_placeholder(row)


def test_informative_discounts_default_false_booleans():
    # DEFAULT_TEMPLATE shape: generic title + description + 4 FALSE excludes
    # (column default) must still read as a placeholder, not 6 "populated".
    row = {"opportunity_title": "General Grant Programme", "funder_name": "X",
           "description": "register-derived analysis text",
           "excludes_individuals": False, "excludes_for_profit": False,
           "excludes_religious": False, "excludes_political": False}
    assert count_populated(row) == 6
    assert count_informative(row) == 2
    assert is_placeholder(row)


def test_informative_counts_true_booleans():
    row = {"opportunity_title": "General Grants", "funder_name": "X",
           "excludes_individuals": True, "description": "d"}
    assert count_informative(row) == 3


def test_beneficiary_substantive_vs_vague():
    assert not is_substantive_beneficiary(None)
    assert not is_substantive_beneficiary("")
    assert not is_substantive_beneficiary([])
    assert not is_substantive_beneficiary(
        "No specific group, or for the benefit of the community")
    assert not is_substantive_beneficiary("Children or young people; "
                                          "Other charities or voluntary bodies")
    assert is_substantive_beneficiary("Children or young people")
    assert is_substantive_beneficiary("Older People in Glasgow")


def test_provenance_groups():
    assert provenance_group("dual-register+website") == "new-dual"
    assert provenance_group("single-register") == "new-single"
    assert provenance_group("DEFAULT_TEMPLATE") == "legacy"
    assert provenance_group("AI_ANALYSIS") == "legacy"
    assert provenance_group(None) == "legacy"


def test_pct_zero_division():
    assert pct(0, 0) == 0.0
    assert pct(1, 4) == 25.0


def test_render_markdown_marks_fail_and_blocked():
    data = {
        "meta": {"at": "2026-10-10T00:00:00+00:00", "db": "grantseeker_devolved",
                 "total_funders": 10, "total_opportunities": 4},
        "scores": {
            "B3_title_specificity": {"specific": 1, "total": 4, "pct": 25.0,
                                    "threshold_pct": 90, "direction": ">=",
                                    "pass": False, "generic_ids": [1, 2, 3]},
            "B6_beneficiary_specificity": {"substantive": 0, "total": 4, "pct": 0.0,
                                           "direction": ">=", "threshold_pct": 80,
                                           "pass": False},
            "B7_default_only": {"placeholder": 3, "total": 4, "pct": 75.0,
                                "direction": "<=", "threshold_pct": 20,
                                "pass": False, "placeholder_ids": [1, 2, 3]},
        },
        "blocked": {k: {"threshold": ">=95%", "status": "BLOCKED",
                        "reason": "r", "unblock": "u"}
                    for k in ("B1_faithfulness", "B5_exclusion_precision",
                              "B8_description_quality")},
        "fill_rates": {
            "overall": {"min_amount": {"filled": 1, "total": 4, "pct": 25.0}},
            "by_provenance": {"new-dual": {"min_amount": {"pct": 50.0}},
                              "new-single": {"min_amount": {"pct": 0.0}},
                              "legacy": {"min_amount": {"pct": 0.0}}},
            "by_register": {"scotland": {"min_amount": {"pct": 25.0}}},
        },
        "source_label_coverage": {"tagged_new": 2, "total": 4,
                                  "tagged_new_pct": 50.0,
                                  "by_source": {"dual-register+website": 2,
                                                "DEFAULT_TEMPLATE": 2}},
        "funder_coverage": [{"reg": "scotland", "funders": 10, "with_opp": 5,
                             "pct_with_opp": 50.0, "with_non_placeholder": 2,
                             "pct_with_non_placeholder": 20.0}],
        "evidence": {"top_titles": [{"value": "General Grants", "count": 3}],
                     "flagged_generic_titles": [{"value": "General Grants",
                                                 "count": 3}],
                     "top_beneficiary_values": [{"value": "<null>", "count": 4}]},
    }
    md = render_markdown(data)
    assert "FAIL" in md and "BLOCKED" in md
    assert "General Grants *" in md
