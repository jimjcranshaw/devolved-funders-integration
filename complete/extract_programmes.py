"""Programme extraction (Issue #5) — DeepSeek only, embeddings off, bounded cost.

Takes grantmaker funders (from a classify sweep file), fetches each website
homepage (bounded chars, 20s timeout), asks DeepSeek for structured
opportunities, stores rows in devolved funding_opportunities covering BOTH
the old cols (funding_amounts, deadlines) and the new E&W cols (min_amount,
max_amount, deadline, eligibility_criteria, beneficiary_groups, excludes_*).

Usage:
  python complete/extract_programmes.py --from complete/sample100_live.json --dry-run --max-funders 3
  python complete/extract_programmes.py --from complete/sample100_live.json --max-funders 2 --out complete/extracted2.json
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

FETCH_TIMEOUT = 20
FETCH_MAX_CHARS = 12000

OPP_COLUMNS = (
    "opportunity_title", "description", "eligibility_inclusion", "eligibility_exclusion",
    "funding_focus", "funding_amounts", "deadlines", "application_process",
    "min_amount", "max_amount", "deadline", "eligibility_criteria",
    "beneficiary_groups", "excludes_individuals", "excludes_for_profit",
    "excludes_religious", "excludes_political",
)


def build_extraction_prompt(funder: dict, page_text: str) -> str:
    return (
        "Extract grant funding opportunities from this charity website text. "
        "Reply with JSON only: {\"opportunities\": [{"
        "\"opportunity_title\": str, \"description\": str, "
        "\"eligibility_inclusion\": str, \"eligibility_exclusion\": str, "
        "\"funding_amounts\": str (verbatim, e.g. '£5,000 - £50,000'), "
        "\"min_amount\": number or null, \"max_amount\": number or null, "
        "\"deadlines\": str (verbatim), \"deadline\": str or null, "
        "\"eligibility_criteria\": str, \"beneficiary_groups\": str, "
        "\"excludes_individuals\": bool, \"excludes_for_profit\": bool, "
        "\"excludes_religious\": bool, \"excludes_political\": bool, "
        "\"application_process\": str}]}. "
        "Use null/false when the page does not state it — never invent amounts or dates.\n"
        f"Funder: {funder.get('name', '')} ({funder.get('charity_number', '')})\n"
        f"Website text:\n{page_text[:FETCH_MAX_CHARS]}"
    )


def parse_opportunities(text: str) -> list[dict]:
    """Extract the opportunities list from model text, robust to prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    opps = obj.get("opportunities", [])
    return [o for o in opps if isinstance(o, dict) and o.get("opportunity_title")]


def map_to_row(opp: dict, funder_id: int) -> dict:
    """Project one extracted opp onto funding_opportunities columns."""
    row: dict = {"funder_id": funder_id, "opportunity_source": "devolved-crawl",
                 "is_default_assessment": False}
    for col in OPP_COLUMNS:
        val = opp.get(col)
        if col.startswith("excludes_"):
            row[col] = bool(val)
        elif col in ("min_amount", "max_amount"):
            row[col] = val if isinstance(val, (int, float)) else None
        else:
            row[col] = str(val)[:2000] if val is not None else None
    return row


def fetch_homepage(url: str) -> str:
    import httpx

    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        resp = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "GrantseekerDevolvedBot/1.0"})
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return ""
    # crude tag strip — homepage signal only, not a full crawl
    import re

    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:FETCH_MAX_CHARS]


def load_targets(path: str, max_funders: int) -> list[dict]:
    data = json.loads(Path(path).read_text())
    results = data.get("results", [])
    grantmakers = [r for r in results if r.get("is_grantmaker")]
    return grantmakers[:max_funders]


def enrich_from_db(targets: list[dict]) -> None:
    """Fill in website/description from funders (sweep files drop them). In place."""
    import psycopg2
    import psycopg2.extras

    ids = [t["id"] for t in targets]
    if not ids:
        return
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
            cur.execute("SELECT id, website, description FROM funders WHERE id = ANY(%s)", (ids,))
            by_id = {r["id"]: r for r in cur.fetchall()}
    finally:
        conn.close()
    for t in targets:
        row = by_id.get(t["id"], {})
        t.setdefault("website", row.get("website", ""))
        t.setdefault("description", row.get("description", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-funders", type=int, default=3)
    ap.add_argument("--ids", default="",
                    help="Comma-separated funder ids to run (subset of file), e.g. --ids 246,256")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    targets = load_targets(args.from_file, args.max_funders)
    if args.ids:
        want = {int(x) for x in args.ids.split(",") if x.strip().isdigit()}
        targets = [t for t in targets if t["id"] in want]
    try:
        enrich_from_db(targets)
    except Exception as e:
        print(f"Warning: DB enrich failed ({e}), continuing without websites.")
    summary: list[dict] = []
    for t in targets:
        page = "" if args.dry_run else fetch_homepage(t.get("website", "") or "")
        prompt = build_extraction_prompt(t, page or "(dry-run: no fetch)")
        if args.dry_run:
            summary.append({"id": t["id"], "name": t["name"], "prompt_chars": len(prompt),
                            "fetched_chars": 0, "stored": 0})
            continue
        if not os.getenv("DEEPSEEK_API_KEY"):
            print("Missing DEEPSEEK_API_KEY in .env.")
            return 2
        from llm.deepseek import evaluate_text

        raw = evaluate_text(prompt)
        try:
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = ""
        opps = parse_opportunities(text)
        rows = [map_to_row(o, t["id"]) for o in opps]
        stored = store_rows(rows) if rows else 0
        summary.append({"id": t["id"], "name": t["name"], "fetched_chars": len(page),
                        "opps_found": len(opps), "stored": stored})
    out = {"sampled": len(targets), "dry_run": args.dry_run, "funders": summary}
    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}: {len(targets)} funders")
    else:
        print(text)
    return 0


def store_rows(rows: list[dict]) -> int:
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "psql-grantsai-db.postgres.database.azure.com"),
        user=os.getenv("DB_USER", "grantsadmin"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DEVOLVED_DB_NAME", "grantseeker_devolved"),
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode="require",
    )
    try:
        cols = ["funder_id"] + [c for c in OPP_COLUMNS if c in rows[0]] + ["opportunity_source", "is_default_assessment"]
        with conn.cursor() as cur:
            execute_values(
                cur,
                f"INSERT INTO funding_opportunities ({', '.join(cols)}) VALUES %s",
                [[r.get(c) for c in cols] for r in rows],
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
