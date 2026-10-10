"""Thin-site triage (DEFAULT_TEMPLATE-only funders) — reachability only, zero AI spend.

Population: funders in staging `grantseeker_devolved` whose funding_opportunities
are ALL `opportunity_source='DEFAULT_TEMPLATE'` (placeholders from the dual
crawl). For each, bucket by REACHABILITY evidence only:

  * crawlable-website — own website + httpx homepage GET (10s) yields
    substantive text (>= MIN_SUBSTANTIVE_CHARS, not a parked domain page).
  * register-only — no/dead website but a register profile URL is buildable
    from charity_number (SC->OSCR, NIC->CCNI via crawl_dual_source.register_url).
  * dead-end — neither a live website nor a register URL.
  * manual-review — top ~50 by GRANT_SIGNAL rule (transparent keyword/GBP-amount
    scoring on the funder description), overriding the reachability bucket;
    the would-have bucket is kept in `reason`.

No browser, no crawl4ai, no LLM/API spend. SELECT + information_schema only;
the session is opened read-only. Staging `grantseeker_devolved` only — refuses
to run against prod (`grantseeker-pg-server`) or any other database.

Usage (from repo root so .env loads):
  python complete/triage_thin.py --out complete/triage_thin_sites.json --report complete/triage_summary.md
  python complete/triage_thin.py --limit 20 --out C:\\temp\\triage_sample.json --report C:\\temp\\triage_sample.md

Outputs (committed-ready; this script never commits):
  * JSON list per funder: id, name, charity_number, source_register, website,
    bucket, reason (+ website_status, register_url, signal_score extras).
  * Markdown summary: counts per bucket + recommended handling + method notes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Explicit .env path: bare load_dotenv() relies on find_dotenv(), which misses
# the repo-root .env under some Windows setups (seen live 2026-10-10).
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complete.crawl_dual_source import register_url  # noqa: E402
from complete.crawl_websites import normalize_url  # noqa: E402

#: Host markers for register-profile pages. Broader than
#: crawl_websites.REGISTER_PROFILE_HOSTS: Oct-9 recrawl packs show CCNI profile
#: URLs (charitycommissionni.org.uk, no hyphen) stored in funders.website and
#: fetched as the "website" leg — they are register evidence, not own sites.
REGISTER_HOST_MARKERS = (
    "oscr.org.uk",
    "charitycommissionni.org.uk",
    "charitycommission.gov.uk",
    "charity-commission",
)


def is_probable_own_website(url: str) -> bool:
    """True for a funder's own site; False for empty or register-profile URLs
    (OSCR/CCNI/GOV.UK commission pages). Pure."""
    if not (url or "").strip():
        return False
    return not any(h in url.lower() for h in REGISTER_HOST_MARKERS)

PLACEHOLDER_SOURCE = "DEFAULT_TEMPLATE"
FETCH_TIMEOUT = 10
MIN_SUBSTANTIVE_CHARS = 500
MANUAL_REVIEW_N = 50
MAX_WORKERS = 20

STAGING_DB = "grantseeker_devolved"

# --- Pure helpers (covered by test_triage_thin.py; no network/DB/LLM) ----------

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<script.*?</script>", re.S | re.I)
STYLE_RE = re.compile(r"<style.*?</style>", re.S | re.I)
WS_RE = re.compile(r"\s+")

#: Lowercase markers of parked/for-sale/error pages. A homepage whose text
#: contains any of these is NOT substantive, however long it is.
PARKED_MARKERS = (
    "domain for sale",
    "buy this domain",
    "this domain is parked",
    "parked domain",
    "domain parking",
    "website coming soon",
    "site is under construction",
    "404 not found",
    "page not found",
)


def strip_html(html: str) -> str:
    """Crude tag strip for homepage-signal text. Pure."""
    text = SCRIPT_RE.sub(" ", html or "")
    text = STYLE_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    return WS_RE.sub(" ", text).strip()


def is_substantive(text: str, minimum: int = MIN_SUBSTANTIVE_CHARS) -> bool:
    """True when homepage text looks like a real site. Pure."""
    clean = (text or "").strip()
    if len(clean) < minimum:
        return False
    low = clean[:2000].lower()
    return not any(marker in low for marker in PARKED_MARKERS)


GBP_RE = re.compile(r"£\s?\d[\d,]*(?:\.\d+)?|(?:\d[\d,]*)\s?(?:million|billion)", re.I)

#: Fund/grant lexicon hits for the manual-review signal (each counted once per
#: funder description, case-insensitive substring).
GRANT_LEXICON = (
    "grant",
    "fund",
    "award",
    "totalling",
    "totaling",
    "distributed",
    "donated",
    "gave",
    "annual giving",
    "trustees meet",
    "application",
    "appeal",
)


def grant_signal_score(description: str) -> int:
    """Transparent grant-size signal: 3 pts per GBP-amount mention + 1 pt per
    distinct fund/grant lexicon word in the description. Pure.

    Used only to rank the top ~50 manual-review candidates; never to invent
    opportunities.
    """
    text = (description or "").lower()
    if not text.strip():
        return 0
    amounts = GBP_RE.findall(text)
    lexicon = sum(1 for word in GRANT_LEXICON if word in text)
    return 3 * len(amounts) + lexicon


def bucket_thin(has_live_website: bool, has_register_url: bool) -> tuple[str, str]:
    """Reachability bucket (manual-review overlay applied later). Pure."""
    if has_live_website:
        return ("crawlable-website",
                "own-site homepage fetched over plain HTTP with substantive text")
    if has_register_url:
        return ("register-only",
                "no live own-site homepage but a register profile URL is buildable")
    return ("dead-end", "no live website and no buildable register profile URL")


# --- Impure helpers (network/DB; not unit-tested live) -------------------------

def staging_guard() -> tuple[str, str, str, int]:
    """Return (host, user, password, port) or raise on any non-staging target."""
    host = os.getenv("DB_HOST", "psql-grantsai-db.postgres.database.azure.com")
    database = os.getenv("DEVOLVED_DB_NAME", STAGING_DB)
    if "grantseeker-pg-server" in (host or ""):
        raise SystemExit("REFUSAL: DB_HOST points at prod (grantseeker-pg-server). "
                         "This lane is staging-only.")
    if database != STAGING_DB:
        raise SystemExit(f"REFUSAL: DEVOLVED_DB_NAME={database!r}; want {STAGING_DB!r}.")
    password = os.getenv("DB_PASSWORD", "")
    if not password:
        raise SystemExit("Missing DB_PASSWORD in .env.")
    return host, os.getenv("DB_USER", "grantsadmin"), password, int(os.getenv("DB_PORT", "5432"))


def fetch_thin_funders(limit: int = 0) -> list[dict]:
    """SELECT-only thin population: funders whose opps are ALL DEFAULT_TEMPLATE.

    Adapts to live columns via information_schema (website/description evidence
    columns may vary by generation).
    """
    import psycopg2
    import psycopg2.extras

    host, user, password, port = staging_guard()
    conn = psycopg2.connect(host=host, user=user, password=password,
                            database=STAGING_DB, port=port, sslmode="require",
                            connect_timeout=30)
    conn.readonly = True  # belt-and-braces: read-only session on top of SELECT-only
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='funders'")
            have = {r["column_name"] for r in cur.fetchall()}
            want = ("website", "description", "charity_number", "source_register")
            cols = ["f.id", "f.name"] + [f'f."{c}"' for c in want if c in have]
            cur.execute(
                "SELECT " + ", ".join(cols) + ", "
                "(SELECT COUNT(*) FROM funding_opportunities o "
                " WHERE o.funder_id = f.id) AS opp_count "
                "FROM funders f "
                "WHERE EXISTS (SELECT 1 FROM funding_opportunities o "
                "              WHERE o.funder_id = f.id "
                "                AND o.opportunity_source = 'DEFAULT_TEMPLATE') "
                "AND NOT EXISTS (SELECT 1 FROM funding_opportunities o "
                "                WHERE o.funder_id = f.id "
                "                  AND o.opportunity_source IS DISTINCT FROM 'DEFAULT_TEMPLATE') "
                "ORDER BY f.id ASC" + (f" LIMIT {int(limit)}" if limit else "")
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def probe_website(url: str, timeout: int = FETCH_TIMEOUT) -> dict:
    """One cheap homepage GET (failures recorded, never retried). No browser."""
    import httpx

    raw = (url or "").strip()
    if not raw:
        return {"live": False, "status": None, "chars": 0, "error": "no-website"}
    target = normalize_url(raw)
    try:
        resp = httpx.get(target, timeout=timeout, follow_redirects=True,
                         headers={"User-Agent": "GrantseekerDevolvedTriage/1.0"})
        if resp.status_code != 200:
            return {"live": False, "status": resp.status_code, "chars": 0,
                    "error": f"http-{resp.status_code}"}
        text = strip_html(resp.text[:20000])
        if is_substantive(text):
            return {"live": True, "status": 200, "chars": len(text), "error": None}
        return {"live": False, "status": 200, "chars": len(text),
                "error": "thin-or-parked-homepage"}
    except Exception as e:  # network/DNS/TLS/timeout — evidence, not fatal
        return {"live": False, "status": None, "chars": 0,
                "error": f"{type(e).__name__}: {e}"[:200]}


# --- Assembly -------------------------------------------------------------------

def triage(funders: list[dict], probes: dict[int, dict]) -> list[dict]:
    """Bucket every funder; manual-review = top-50 grant-signal overlay. Pure
    apart from its inputs (probes computed by the caller)."""
    scored = [(f, grant_signal_score(f.get("description") or "")) for f in funders]
    ranked = sorted(scored, key=lambda t: (-t[1], t[0].get("id", 0)))
    manual_ids: set[int] = set()
    for funder, score in ranked[:MANUAL_REVIEW_N]:
        if score > 0:
            manual_ids.add(funder["id"])

    records = []
    for funder in funders:
        fid = funder["id"]
        probe = probes.get(fid, {"live": False, "status": None, "chars": 0,
                                 "error": "not-probed"})
        stored_website = (funder.get("website") or "").strip()
        own = is_probable_own_website(stored_website)
        live_site = bool(own and probe.get("live"))
        _reg, reg_url = register_url(funder.get("charity_number") or "")
        has_reg = bool(reg_url)
        bucket, reason = bucket_thin(live_site, has_reg)
        score = grant_signal_score(funder.get("description") or "")
        if fid in manual_ids:
            reason = (f"manual-review pick (grant-signal score {score}; "
                      f"would-be bucket: {bucket}) — {reason}")
            bucket = "manual-review"
        elif not own and stored_website:
            reason += ("; website field holds a register-profile URL, not an own site"
                       if any(h in stored_website.lower() for h in REGISTER_HOST_MARKERS)
                       else "; website field holds a non-own URL")
        elif not stored_website:
            reason += "; no website stored"
        else:
            detail = probe.get("error") or f"homepage {probe.get('chars', 0)} chars"
            reason += f"; website probe: {detail}"
        records.append({
            "id": fid,
            "name": funder.get("name", ""),
            "charity_number": funder.get("charity_number") or "",
            "source_register": funder.get("source_register") or "",
            "website": funder.get("website") or "",
            "bucket": bucket,
            "reason": reason,
            "website_status": probe.get("status"),
            "website_chars": probe.get("chars", 0),
            "register_url": reg_url or "",
            "signal_score": score,
            "opp_count": funder.get("opp_count"),
        })
    return records


HANDLING = {
    "crawlable-website": "Re-crawl with the dual-source pipeline (website leg will now "
                         "carry the page); register leg optional. Automatable.",
    "register-only": "Single-register fetch + DeepSeek pass (cheap, bounded); accept "
                     "that many will yield trustee-discretion rows with no public "
                     "programme. Automatable with human spot-checks.",
    "dead-end": "Do not spend crawl/LLM budget. Keep the DEFAULT_TEMPLATE row as an "
                "explicit thin marker, or park for a future bulk-register refresh.",
    "manual-review": "Human pass first (~50 highest grant-signal descriptions): confirm "
                     "whether a real fund exists, then route to the matching lane above.",
}

BUCKET_ORDER = ("crawlable-website", "register-only", "dead-end", "manual-review")


def build_summary(records: list[dict], probed: int, at: str) -> str:
    from collections import Counter
    counts = Counter(r["bucket"] for r in records)
    total = len(records)
    lines = [
        "# Thin-site triage — DEFAULT_TEMPLATE-only funders",
        "",
        f"Source: staging `{STAGING_DB}` (SELECT-only, read-only session) at {at}.",
        f"Population: {total} funders whose opportunities are ALL "
        f"`{PLACEHOLDER_SOURCE}`; homepages probed once each over plain httpx "
        f"({FETCH_TIMEOUT}s timeout, no retries, no browser, no LLM).",
        "",
        "## Counts per bucket",
        "",
        "| Bucket | Funders | % |",
        "|--------|---------|---|",
    ]
    for bucket in BUCKET_ORDER:
        n = counts.get(bucket, 0)
        pct = (100.0 * n / total) if total else 0.0
        lines.append(f"| {bucket} | {n} | {pct:.1f}% |")
    lines += ["", "## Recommended handling", ""]
    for bucket in BUCKET_ORDER:
        lines += [f"### {bucket} ({counts.get(bucket, 0)})", "", HANDLING[bucket], ""]
    lines += [
        "## Method notes (reproducible)",
        "",
        f"* Website liveness = one httpx GET of the stored website homepage "
        f"({FETCH_TIMEOUT}s, follow redirects); live means HTTP 200 + "
        f">= {MIN_SUBSTANTIVE_CHARS} chars of tag-stripped text with none of the "
        f"parked-domain markers ({', '.join(PARKED_MARKERS[:4])}, …). "
        f"Probed {probed} own-site homepages.",
        "* Own-site test is stricter than crawl_websites.is_own_website: OSCR, "
        "CCNI (charitycommissionni.org.uk) and commission URLs count as register "
        "evidence, since Oct-9 recrawl packs show CCNI profile URLs stored in "
        "funders.website.",
        "* Register URL built purely from `charity_number`: `SC…` → OSCR profile, "
        "`NIC…` → CCNI profile (`complete/crawl_dual_source.register_url`).",
        f"* Manual-review = top {MANUAL_REVIEW_N} by grant-signal score on the "
        "funder description: 3 pts per GBP-amount mention "
        "(`£12,000` / `2 million` style) + 1 pt per distinct fund/grant lexicon "
        f"word ({', '.join(GRANT_LEXICON)}). Ties break by lowest funder id; "
        "score 0 never qualifies, so the bucket can hold fewer than "
        f"{MANUAL_REVIEW_N}.",
        "* Re-run: `python complete/triage_thin.py --out complete/triage_thin_sites.json "
        "--report complete/triage_summary.md` (staging only, read-only, zero AI spend).",
        "",
    ]
    manual = [r for r in records if r["bucket"] == "manual-review"]
    if manual:
        lines += ["## Manual-review shortlist (top grant-signal)", ""]
        lines += ["| ID | Name | Score | Would-be bucket | Website |", "|---|---|---|---|---|"]
        for r in sorted(manual, key=lambda r: (-r["signal_score"], r["id"])):
            would = r["reason"].split("would-be bucket: ", 1)[1].split(")", 1)[0] \
                if "would-be bucket:" in r["reason"] else "?"
            lines.append(f"| {r['id']} | {r['name']} | {r['signal_score']} | "
                         f"{would} | {r['website'] or '—'} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Triage DEFAULT_TEMPLATE-only funders "
                                 "by reachability (no browser, no LLM, read-only).")
    ap.add_argument("--out", default="complete/triage_thin_sites.json")
    ap.add_argument("--report", default="complete/triage_summary.md")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap funders for smoke tests (0 = all).")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--timeout", type=int, default=FETCH_TIMEOUT)
    args = ap.parse_args(argv)

    funders = fetch_thin_funders(limit=args.limit)
    print(f"Thin funders (all-{PLACEHOLDER_SOURCE}): {len(funders)}", flush=True)
    targets = {f["id"]: (f.get("website") or "") for f in funders
               if is_probable_own_website(f.get("website") or "")}
    print(f"Own-site homepages to probe: {len(targets)}", flush=True)
    probes: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        future_to_id = {pool.submit(probe_website, url, args.timeout): fid
                        for fid, url in targets.items()}
        done = 0
        for fut, fid in future_to_id.items():
            try:
                probes[fid] = fut.result()
            except Exception as e:  # pragma: no cover — defensive; probe catches its own
                probes[fid] = {"live": False, "status": None, "chars": 0,
                               "error": f"harness: {e}"[:200]}
            done += 1
            if done % 100 == 0:
                print(f"  probed {done}/{len(targets)}", flush=True)
    records = triage(funders, probes)
    at = datetime.now(timezone.utc).isoformat()
    out_path, report_path = Path(args.out), Path(args.report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"at": at, "count": len(records),
                                    "funders": records}, indent=1), encoding="utf-8")
    report_path.write_text(build_summary(records, len(targets), at), encoding="utf-8")
    from collections import Counter
    counts = Counter(r["bucket"] for r in records)
    print(f"Wrote {out_path} ({len(records)} funders) + {report_path} "
          f"{dict(counts)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
