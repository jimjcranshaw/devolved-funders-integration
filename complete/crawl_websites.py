"""Website crawl for devolved grantmakers (Issue #5) — reuses the E&W crawler.

Reuses v3 `regular_website_pipeline.crawl_foundation` (sitemap + filtered
subpages, crawl4ai) and `analyze_foundation_content` (DeepSeek). Stores via
our `extract_programmes.map_to_row/store_rows`, so the new E&W columns are
covered and NO embeddings/OpenAI are touched.

Targets: classified grantmakers WITH their own websites. Register-profile
URLs (oscr.org.uk, charitycommission) are skipped here — they belong to the
oscr/ccni profile pipelines.

Usage:
  python complete/crawl_websites.py --from complete/sample100_live.json --dry-run
  python complete/crawl_websites.py --from complete/sample100_live.json --max-funders 2 --out complete/crawled2.json
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

V2_CODEBASE_PATH = os.path.join(
    os.path.expanduser("~"),
    "OneDrive", "Documents", "Consultancy", "Current projects",
    "Grants AI", "Development", "Claude Code", "v3",
)
sys.path.insert(0, V2_CODEBASE_PATH)

REGISTER_PROFILE_HOSTS = ("oscr.org.uk", "charitycommission.gov.uk", "charity-commission")


def is_own_website(url: str) -> bool:
    """True for a funder's own site; False for register-profile pages or empty."""
    if not url:
        return False
    low = url.lower()
    return not any(h in low for h in REGISTER_PROFILE_HOSTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-funders", type=int, default=5)
    ap.add_argument("--ids", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from complete.extract_programmes import enrich_from_db, map_to_row, store_rows
    from regular_website_pipeline import analyze_foundation_content, crawl_foundation

    data = json.loads(Path(args.from_file).read_text())
    grantmakers = [r for r in data.get("results", []) if r.get("is_grantmaker")]
    if args.ids:
        want = {int(x) for x in args.ids.split(",") if x.strip().isdigit()}
        grantmakers = [g for g in grantmakers if g["id"] in want]
    grantmakers = grantmakers[: args.max_funders]
    try:
        enrich_from_db(grantmakers)
    except Exception as e:
        print(f"Warning: DB enrich failed ({e}).")
    targets = [g for g in grantmakers if is_own_website(g.get("website", ""))]
    skipped = len(grantmakers) - len(targets)

    summary: list[dict] = []
    for t in targets:
        if args.dry_run:
            summary.append({"id": t["id"], "name": t["name"],
                            "website": t.get("website", ""), "crawled": False})
            continue
        pages = asyncio.run(crawl_foundation(t["website"], t["name"]))
        if not pages:
            # Never analyse zero pages: the model backfills from training
            # data and the row would be unfaithful (seen live on Gannochy).
            summary.append({"id": t["id"], "name": t["name"], "pages": 0,
                            "opps_found": 0, "stored": 0, "note": "no pages, skipped"})
            continue
        analysis = asyncio.run(analyze_foundation_content(pages, t["name"]))
        opps = analysis.get("opportunities", [])
        rows = [map_to_row(o, t["id"]) for o in opps if isinstance(o, dict)]
        stored = store_rows(rows) if rows else 0
        summary.append({"id": t["id"], "name": t["name"], "pages": len(pages),
                        "opps_found": len(opps), "stored": stored})
    out = {"sampled": len(targets), "skipped_register_profiles": skipped,
           "dry_run": args.dry_run, "funders": summary}
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}: {len(targets)} crawled, {skipped} skipped")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
