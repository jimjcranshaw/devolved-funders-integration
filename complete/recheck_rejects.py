"""Second-pass recheck (Issue #5): homepage text for low-confidence rejects.

For sweep rejects below --threshold confidence, fetches each website homepage
(cheap httpx, no browser) and re-asks DeepSeek with the page text appended.
Records old vs new verdicts; flips are added to the grantmaker queue.

Usage:
  python complete/recheck_rejects.py --from complete/full_live.json --dry-run
  python complete/recheck_rejects.py --from complete/full_live.json --out complete/rechecked.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--out", default="")
    ap.add_argument("--progress", default="complete/rechecked_progress.jsonl",
                    help="Heartbeat: one JSON line appended per funder")
    args = ap.parse_args()

    import os

    from complete.classify_grantmakers import build_prompt, parse_verdict
    from complete.extract_programmes import enrich_from_db, fetch_homepage

    data = json.loads(Path(args.from_file).read_text())
    cands = [r for r in data.get("results", [])
             if not r.get("is_grantmaker") and (r.get("confidence") or 0) < args.threshold]
    try:
        enrich_from_db(cands)
    except Exception as e:
        print(f"Warning: DB enrich failed ({e}).")
    if args.dry_run:
        with_site = sum(1 for c in cands if c.get("website"))
        print(f"{len(cands)} low-confidence rejects, {with_site} with websites")
        return 0
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("Missing DEEPSEEK_API_KEY in .env.")
        return 2
    from llm.deepseek import evaluate_text

    checked, flips = [], []
    total = len(cands)
    progress_fh = open(args.progress, "w") if not args.dry_run else None
    try:
        for n, c in enumerate(cands, start=1):
            page = fetch_homepage(c.get("website", "") or "")
            if not page:
                rec = {"id": c["id"], "name": c["name"], "fetched": False}
                checked.append(rec)
            else:
                raw = evaluate_text(build_prompt(c, page))
                try:
                    text = raw["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError):
                    text = ""
                v = parse_verdict(text)
                rec = {"id": c["id"], "name": c["name"], "fetched": True,
                       "old_conf": c.get("confidence"), **v}
                checked.append(rec)
                if v.get("is_grantmaker"):
                    flips.append(rec)
            progress_fh.write(json.dumps({"n": n, "of": total, "id": c["id"],
                                          "verdict": rec.get("is_grantmaker"),
                                          "flips_so_far": len(flips)}) + "\n")
            progress_fh.flush()
            print(f"[{n}/{total}] id {c['id']} grantmaker={rec.get('is_grantmaker')} "
                  f"flips={len(flips)}", flush=True)
    finally:
        if progress_fh:
            progress_fh.close()
    out = {"candidates": len(cands), "checked": len(checked),
           "flips": flips, "records": checked, "dry_run": False}
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}: {len(checked)} rechecked, {len(flips)} flipped to grantmaker")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
