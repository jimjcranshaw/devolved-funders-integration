"""Grantmaker pre-filter (Issue #5 scope) — DeepSeek only, bounded cost.

Usage:
  python complete/classify_grantmakers.py --dry-run --max-funders 20
  python complete/classify_grantmakers.py --dry-run --max-funders 20 --out complete/sample20.json
  python complete/classify_grantmakers.py --max-funders 5 --out complete/sample5_live.json   # real DeepSeek calls

Reads no-opp funders live from grantseeker_devolved (single account), builds
one bounded prompt per funder, one DeepSeek call each. --dry-run sends nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

CONTEXT_CANDIDATES = ("description", "website", "oscr_purposes", "purposes", "activities", "objects")


def build_prompt(funder: dict, page_text: str = "") -> str:
    bits = [
        f"Name: {funder.get('name', '')}",
        f"Charity number: {funder.get('charity_number', '')} ({funder.get('source_register', '')})",
    ]
    for col in CONTEXT_CANDIDATES:
        if funder.get(col):
            bits.append(f"{col}: {str(funder[col])[:1500]}")
    if page_text:
        bits.append(f"website homepage text: {page_text[:4000]}")
    return (
        "Decide if this charity is a GRANTMAKER (gives grants to other organisations) "
        "or not (church, school, service-delivery charity, club). "
        "Reply with JSON only: {\"is_grantmaker\": true/false, \"confidence\": 0-1, \"reason\": \"<15 words>\"}.\n"
        + "\n".join(bits)
    )


def parse_verdict(text: str) -> dict:
    """Extract {is_grantmaker, confidence, reason} from model text, robust to prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"is_grantmaker": False, "confidence": 0.0, "reason": "unparseable"}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {"is_grantmaker": False, "confidence": 0.0, "reason": "unparseable"}
    return {
        "is_grantmaker": bool(obj.get("is_grantmaker", False)),
        "confidence": float(obj.get("confidence", 0.0) or 0.0),
        "reason": str(obj.get("reason", ""))[:120],
    }


def live_no_opp_funders(limit: int, offset: int = 0) -> tuple[list[dict], list[str]]:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "psql-grantsai-db.postgres.database.azure.com"),
        user=os.getenv("DB_USER", "grantsadmin"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DEVOLVED_DB_NAME", "grantseeker_devolved"),
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode="require",
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='funders'")
            cols = {r["column_name"] for r in cur.fetchall()}
            ctx = [c for c in CONTEXT_CANDIDATES if c in cols]
            extra = "".join(f', f."{c}"' for c in ctx)
            cur.execute(
                f"SELECT f.id, f.name, f.charity_number, f.source_register{extra} "
                f"FROM funders f WHERE NOT EXISTS "
                f"(SELECT 1 FROM funding_opportunities o WHERE o.funder_id = f.id) "
                f"ORDER BY f.id ASC LIMIT {int(limit)} OFFSET {int(offset)}",
            )
            return [dict(r) for r in cur.fetchall()], ctx
    finally:
        conn.close()


def merge_sweeps(parts: list[dict]) -> dict:
    """Combine per-chunk sweep files into one full sweep. Pure (tested)."""
    results: list[dict] = []
    ctx: set[str] = set()
    for p in parts:
        results.extend(p.get("results", []))
        ctx.update(p.get("context_cols", []))
    seen, deduped = set(), []
    for r in sorted(results, key=lambda x: x.get("id", 0)):
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        deduped.append(r)
    return {"sampled": len(deduped), "context_cols": sorted(ctx),
            "dry_run": all(p.get("dry_run", False) for p in parts),
            "grantmakers": sum(1 for r in deduped if r.get("is_grantmaker")),
            "results": deduped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-funders", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--merge", nargs="*", default=None,
                    help="Merge chunk files: --merge c0.json c1.json --out full.json")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.merge is not None:
        parts = [json.loads(Path(p).read_text()) for p in args.merge]
        merged = merge_sweeps(parts)
        text = json.dumps(merged, indent=2)
        if args.out:
            Path(args.out).write_text(text)
            print(f"Merged {len(parts)} chunks: {merged['sampled']} funders, "
                  f"{merged['grantmakers']} grantmakers -> {args.out}")
        else:
            print(text)
        return 0
    if not os.getenv("DB_PASSWORD"):
        print("Missing DB_PASSWORD in .env.")
        return 2
    funders, ctx = live_no_opp_funders(args.max_funders, args.offset)
    results = []
    for f in funders:
        prompt = build_prompt(f)
        if args.dry_run:
            results.append({"id": f["id"], "name": f["name"], "prompt_chars": len(prompt), "sent": False})
            continue
        from llm.deepseek import evaluate_text

        raw = evaluate_text(prompt)
        try:
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = ""
        results.append({"id": f["id"], "name": f["name"], **parse_verdict(text), "sent": True})
    summary = {
        "sampled": len(funders),
        "context_cols": ctx,
        "dry_run": args.dry_run,
        "grantmakers": sum(1 for r in results if r.get("is_grantmaker")),
        "results": results,
    }
    text = json.dumps(summary, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}: {summary['sampled']} sampled, {summary['grantmakers']} grantmakers")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
