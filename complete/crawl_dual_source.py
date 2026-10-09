"""Dual-source crawl (Issue #5): register profile + own website, merged.

For EVERY funder, both sources are attempted:
  - register profile (OSCR/CCNI URL built from charity_number, single fetch)
  - own website (crawl_websites.crawl_site subpage loop)
Combined pages go through ONE DeepSeek analysis; rows are tagged with which
source(s) fed them. Single-source funders are kept and flagged, never dropped.

A human review pack (markdown) is written per funder alongside.

Usage:
  python complete/crawl_dual_source.py --from complete/sample100_live.json --ids 256 --dry-run
  python complete/crawl_websites.py --from ... (website-only path, unchanged)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

OSCR_PROFILE_URL = ("https://www.oscr.org.uk/about-charities/search-the-register/"
                    "charity-details?number={num}")
CCNI_PROFILE_URL = ("https://www.charitycommissionni.org.uk/charity-details/"
                    "?regId={num}&subId=0")


def register_url(charity_number: str) -> tuple[str, str]:
    """Return (register, profile_url) for a charity number, or ('', '') if none."""
    num = (charity_number or "").strip().upper()
    if num.startswith("SC"):
        return ("scotland", OSCR_PROFILE_URL.format(num=num))
    if num.startswith("NIC"):
        return ("northern_ireland", CCNI_PROFILE_URL.format(num=num))
    return ("", "")


async def fetch_register_page(url: str) -> dict | None:
    """Single-page fetch of a register profile. None on any failure."""
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig

    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(wait_until="domcontentloaded", page_timeout=30000,
                                 cache_mode="bypass")
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
    except Exception:
        return None
    if not (result.success and result.markdown):
        return None
    return {"url": url, "title": (result.metadata or {}).get("title", ""),
            "content": result.markdown[:50000], "source": "register"}


MACHINE_SOURCES = ("devolved-crawl", "single-website", "single-register",
                   "dual-website+register", "dual-register+website")


def replace_machine_rows(funder_id: int) -> int:
    """Delete prior machine-extracted rows so recrawls never duplicate. Returns count."""
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "psql-grantsai-db.postgres.database.azure.com"),
        user=os.getenv("DB_USER", "grantsadmin"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DEVOLVED_DB_NAME", "grantseeker_devolved"),
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode="require",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM funding_opportunities WHERE funder_id = %s "
                        "AND opportunity_source = ANY(%s)",
                        (funder_id, list(MACHINE_SOURCES)))
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def build_review_pack(funder: dict, pages: list[dict], opps: list[dict],
                      sources: str) -> str:
    """Human review markdown: what was read, what was extracted. Pure."""
    lines = [f"# {funder.get('name', '')} (id {funder.get('id', '')})", "",
             f"Charity number: {funder.get('charity_number', '')} | Sources: {sources}", "",
             f"## Pages read ({len(pages)})"]
    for p in pages:
        lines.append(f"- [{p.get('source', 'website')}] {p.get('url', '')} "
                     f"({len(p.get('content', ''))} chars)")
    lines += ["", f"## Extracted opportunities ({len(opps)})"]
    for o in opps:
        lines.append(f"### {o.get('opportunity_title', 'Untitled')}")
        for key in ("description", "funding_amounts", "min_amount", "max_amount",
                    "deadlines", "deadline", "eligibility_criteria", "beneficiary_groups"):
            if o.get(key) not in (None, "", "Not specified on the website.",
                                  "Not specified on the provided page."):
                lines.append(f"- **{key}**: {o.get(key)}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-funders", type=int, default=3)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--ids", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--review-dir", default="review")
    ap.add_argument("--resume", action="store_true",
                    help="Skip funders that already have a review pack in --review-dir")
    args = ap.parse_args()

    from complete.crawl_websites import crawl_site, is_own_website, normalize_url
    from complete.extract_programmes import enrich_from_db, map_to_row, store_rows
    from regular_website_pipeline import analyze_foundation_content

    data = json.loads(Path(args.from_file).read_text())
    grantmakers = [r for r in data.get("results", []) if r.get("is_grantmaker")]
    if args.ids:
        want = {int(x) for x in args.ids.split(",") if x.strip().isdigit()}
        grantmakers = [g for g in grantmakers if g["id"] in want]
    grantmakers = grantmakers[args.offset: args.offset + args.max_funders]
    try:
        enrich_from_db(grantmakers)
    except Exception as e:
        print(f"Warning: DB enrich failed ({e}).")

    review_dir = Path(args.review_dir)
    review_dir.mkdir(exist_ok=True)
    if args.resume:
        before = len(grantmakers)
        grantmakers = [g for g in grantmakers if not (review_dir / f"{g['id']}.md").exists()]
        print(f"Resume: {before} queued, {len(grantmakers)} remaining", flush=True)
    summary: list[dict] = []
    total = len(grantmakers)
    for n, t in enumerate(grantmakers, start=1):
        reg, reg_url = register_url(t.get("charity_number", ""))
        own = is_own_website(t.get("website", ""))
        if args.dry_run:
            summary.append({"id": t["id"], "name": t["name"],
                            "register": reg or None, "has_website": own,
                            "plan": "+".join([s for s in (["register" if reg else None] +
                                               (["website"] if own else [])) if s]) or "no-source"})
            continue
        pages: list[dict] = []
        if reg_url:
            rp = asyncio.run(fetch_register_page(reg_url))
            if rp:
                pages.append(rp)
        if own:
            for p in asyncio.run(crawl_site(normalize_url(t["website"]), t["name"])):
                pages.append({**p, "source": "website"})
        if not pages:
            summary.append({"id": t["id"], "name": t["name"], "pages": 0,
                            "opps_found": 0, "stored": 0, "note": "no source reachable"})
            print(f"[{n}/{total}] id {t['id']} no-source", flush=True)
            continue
        used = sorted({p.get("source", "website") for p in pages})
        sources = "+".join(used)
        analysis = asyncio.run(analyze_foundation_content(pages, t["name"]))
        opps = [o for o in analysis.get("opportunities", []) if isinstance(o, dict)]
        rows = [map_to_row(o, t["id"]) for o in opps if o.get("opportunity_title")]
        for r in rows:
            r["opportunity_source"] = f"dual-{sources}" if len(used) > 1 else f"single-{sources}"
        replaced = replace_machine_rows(t["id"]) if rows else 0
        stored = store_rows(rows) if rows else 0
        review_dir.mkdir(exist_ok=True)
        (review_dir / f"{t['id']}.md").write_text(build_review_pack(t, pages, opps, sources))
        summary.append({"id": t["id"], "name": t["name"], "pages": len(pages),
                        "sources": sources, "opps_found": len(opps),
                        "replaced": replaced, "stored": stored})
        print(f"[{n}/{total}] id {t['id']} pages={len(pages)} src={sources} "
              f"opps={len(opps)} stored={stored} replaced={replaced}", flush=True)
    out = {"sampled": len(grantmakers), "dry_run": args.dry_run, "funders": summary}
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}: {len(grantmakers)} funders")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
