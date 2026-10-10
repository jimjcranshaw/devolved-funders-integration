"""Tests for complete/triage_thin.py pure helpers (no DB/network/LLM)."""

from complete.triage_thin import (
    bucket_thin,
    build_summary,
    grant_signal_score,
    is_probable_own_website,
    is_substantive,
    strip_html,
    triage,
)


def test_strip_html_drops_tags_scripts_styles():
    html = ("<html><head><style>.x{color:red}</style></head><body>"
            "<script>alert(1)</script><h1>Grants for youth</h1><p>Apply now</p></body></html>")
    text = strip_html(html)
    assert "Grants for youth" in text and "Apply now" in text
    assert "<" not in text and "alert" not in text and "color" not in text


def test_is_substantive_needs_length():
    assert not is_substantive("x" * 499)
    assert is_substantive("grants " * 100)


def test_is_substantive_rejects_parked_pages():
    body = ("This domain for sale, buy this domain now. " * 40)[:3000]
    assert not is_substantive(body)


def test_grant_signal_scores_amounts_and_lexicon():
    assert grant_signal_score("") == 0
    assert grant_signal_score("A small community choir.") == 0
    text = "The Trust awarded grants totalling £120,000 to youth groups."
    # 1 GBP amount (3 pts) + grant/awarded?/totalling lexicon hits
    assert grant_signal_score(text) >= 3 + 2
    assert grant_signal_score("£5,000 and £10,000 given") > grant_signal_score("£5,000 given")


def test_is_probable_own_website_rejects_register_profiles():
    assert is_probable_own_website("https://example.org/grants")
    assert is_probable_own_website("example.org")
    assert not is_probable_own_website("")
    assert not is_probable_own_website(
        "https://www.charitycommissionni.org.uk/charity-details/?regId=100084&subId=0")
    assert not is_probable_own_website(
        "https://www.oscr.org.uk/about-charities/search-the-register/charity-details?number=SC012059")


def test_triage_ccni_profile_website_is_register_only():
    rec = triage([_funder(
        11, charity_number="NIC100084",
        website="https://www.charitycommissionni.org.uk/charity-details/?regId=100084&subId=0")],
        {})[0]
    assert rec["bucket"] == "register-only"
    assert "not an own site" in rec["reason"]


def test_bucket_thin_reachability_only():
    assert bucket_thin(True, True)[0] == "crawlable-website"
    assert bucket_thin(True, False)[0] == "crawlable-website"
    assert bucket_thin(False, True)[0] == "register-only"
    assert bucket_thin(False, False)[0] == "dead-end"


def _funder(fid: int, **kw) -> dict:
    base = {"id": fid, "name": f"Funder {fid}", "charity_number": "SC000001",
            "source_register": "scotland", "website": "", "description": "",
            "opp_count": 1}
    base.update(kw)
    return base


def test_triage_manual_review_overlay_top_signal():
    rich = _funder(1, description="Awarded grants totalling £2 million across Scotland. "
                                  "Applications open yearly; trustees meet quarterly.",
                   website="https://example.org")
    plain = _funder(2, description="A small local group.")
    records = triage([rich, plain],
                     {1: {"live": True, "status": 200, "chars": 900, "error": None}})
    by_id = {r["id"]: r for r in records}
    assert by_id[1]["bucket"] == "manual-review"
    assert "would-be bucket: crawlable-website" in by_id[1]["reason"]
    assert by_id[2]["bucket"] in ("register-only", "dead-end")
    for r in records:
        for key in ("id", "name", "charity_number", "source_register",
                    "website", "bucket", "reason"):
            assert key in r


def test_triage_manual_review_skips_zero_scores_and_caps_fifty():
    funders = [_funder(i, description="No signal here.") for i in range(1, 80)]
    records = triage(funders, {})
    assert all(r["bucket"] != "manual-review" for r in records)


def test_triage_register_url_from_charity_number():
    rec = triage([_funder(9, charity_number="NIC100001")], {})[0]
    assert rec["bucket"] == "register-only"
    assert "charitycommissionni" in rec["register_url"]
    rec_ew = triage([_funder(10, charity_number="1234567")], {})[0]
    assert rec_ew["bucket"] == "dead-end"
    assert rec_ew["register_url"] == ""


def test_build_summary_counts_and_handling():
    records = triage([
        _funder(1, website="https://example.org", charity_number="SC1",
                description="Grants totalling £1 million awarded yearly."),
        _funder(2, charity_number="SC2"),
        _funder(3, charity_number=""),
    ], {1: {"live": True, "status": 200, "chars": 800, "error": None}})
    md = build_summary(records, probed=1, at="2026-10-10T00:00:00+00:00")
    assert "| manual-review | 1 |" in md
    assert "| register-only | 1 |" in md
    assert "| dead-end | 1 |" in md
    assert "Recommended handling" in md and "Re-run:" in md
