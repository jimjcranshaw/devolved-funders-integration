"""No-opportunity gap lister (Issue #1 -> scopes #5) — read-only, single account.

Usage:
  python audit/gaps.py --out audit/gaps.json --limit 5000
  python audit/gaps.py --register scotland --out audit/gaps_scotland.json

Adapts to live columns: discovers amount/deadline-ish fields via
information_schema instead of assuming a schema generation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

AMOUNT_CANDIDATES = ("typical_value_band", "funding_amounts", "min_amount", "max_amount", "amount")
DEADLINE_CANDIDATES = ("deadlines", "deadline", "next_deadline", "application_deadline")


def pick_present(want: tuple[str, ...], have: set[str]) -> list[str]:
    have_lower = {c.lower() for c in have}
    return [c for c in want if c.lower() in have_lower]


def summarize(rows: list[dict], amount_cols: list[str], deadline_cols: list[str]) -> dict:
    """Pure summary: per-register no-opp counts + missing-field rates."""
    by_reg: dict[str, dict] = {}
    for r in rows:
        reg = r.get("source_register") or "unknown"
        b = by_reg.setdefault(reg, {"no_opp_funders": 0, "missing_amount": 0, "missing_deadline": 0})
        b["no_opp_funders"] += 1
        if amount_cols and all(not r.get(c) for c in amount_cols):
            b["missing_amount"] += 1
        if deadline_cols and all(not r.get(c) for c in deadline_cols):
            b["missing_deadline"] += 1
    return {"total_no_opp": len(rows), "by_register": by_reg,
            "amount_cols": amount_cols, "deadline_cols": deadline_cols}


def live_gaps(register: str = "", limit: int = 5000) -> dict:
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
            amount_cols = pick_present(AMOUNT_CANDIDATES, cols)
            deadline_cols = pick_present(DEADLINE_CANDIDATES, cols)
            extra = ",".join(f'f."{c}"' for c in (*amount_cols, *deadline_cols))
            extra_sql = f", {extra}" if extra else ""
            where = "AND f.source_register = %s" if register else ""
            params: list = [register] if register else []
            cur.execute(
                f"SELECT f.id, f.name, f.charity_number, f.source_register{extra_sql} "
                f"FROM funders f WHERE NOT EXISTS "
                f"(SELECT 1 FROM funding_opportunities o WHERE o.funder_id = f.id) "
                f"{where} ORDER BY f.id ASC LIMIT {int(limit)}",
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {**summarize(rows, amount_cols, deadline_cols), "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--register", default="")
    ap.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args()
    if not os.getenv("DB_PASSWORD"):
        print("Missing DB_PASSWORD in .env.")
        return 2
    data = live_gaps(register=args.register, limit=args.limit)
    text = json.dumps(data, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        s = data
        print(f"Wrote {args.out}: {s['total_no_opp']} no-opp funders "
              f"(amount cols: {s['amount_cols'] or 'none'}, deadline cols: {s['deadline_cols'] or 'none'})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
