"""Tests for complete/crawl_dual_source.py pure helpers (no crawl/API/DB)."""

from complete.crawl_dual_source import build_review_pack, register_url


def test_register_url_scotland():
    reg, url = register_url("SC025083")
    assert reg == "scotland" and "SC025083" in url and "oscr.org.uk" in url


def test_register_url_ni():
    reg, url = register_url("nic103321")
    assert reg == "northern_ireland" and "charitycommissionni" in url


def test_register_url_ew_none():
    assert register_url("1234567") == ("", "")


def test_review_pack_marks_sources_and_skips_filler():
    funder = {"id": 256, "name": "The Gannochy Trust", "charity_number": "SC003133"}
    pages = [{"url": "https://x", "content": "abc", "source": "website"},
             {"url": "https://oscr", "content": "def", "source": "register"}]
    opps = [{"opportunity_title": "T", "funding_amounts": "£10k",
             "deadlines": "Not specified on the website."}]
    md = build_review_pack(funder, pages, opps, "register+website")
    assert "register+website" in md and "£10k" in md
    assert "Not specified on the website." not in md
