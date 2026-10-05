"""Tests for complete/crawl_websites.py pure helper (no crawl/API/DB)."""

from complete.crawl_websites import is_own_website, normalize_url


def test_own_website_accepted():
    assert is_own_website("https://www.robertsontrust.org.uk/") is True


def test_register_profiles_skipped():
    assert is_own_website("https://www.oscr.org.uk/about-charities/search-the-register/charity-details?number=SC123") is False
    assert is_own_website("https://register-of-charities.charitycommission.gov.uk/charity-search/-/charity-details/123") is False


def test_empty_or_bare():
    assert is_own_website("") is False
    assert is_own_website("www.gannochytrust.org.uk") is True


def test_normalize_adds_scheme():
    assert normalize_url("www.gannochytrust.org.uk") == "https://www.gannochytrust.org.uk"
    assert normalize_url("http://example.org/") == "http://example.org/"
    assert normalize_url("") == ""
