"""Live schema snapshot (Issue #2) — read-only, single account, no AI.

Usage:
  python integrate/snapshot_schema.py --out integrate/schema_snapshot.json

Captures information_schema columns for funders / funding_opportunities /
pages (missing tables recorded, not errors) plus a gap summary vs the
reconcile targets. Applies nothing.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TABLES = ("funders", "funding_opportunities", "pages")
OPP_AMOUNT_CANDIDATES = ("amount", "min_amount", "max_amount", "funding_amounts",
                         "typical_value_band", "value_band", "grant_amount")
OPP_DEADLINE_CANDIDATES = ("deadline", "deadlines", "next_deadline",
                           "application_deadline", "closing_date")


def summarize_gaps(snapshot: dict[str, list[str]]) -> dict:
    """Pure gap summary from a {table: [columns]} snapshot."""
    lower = {t: {c.lower() for c in cols} for t, cols in snapshot.items()}
    funders = lower.get("funders", set())
    opps = lower.get("funding_opportunities", set())
    return {
        "funders_missing_source_register": "source_register" not in funders,
        "funders_missing_oscr_purposes": "oscr_purposes" not in funders,
        "opp_amount_cols_present": sorted(c for c in OPP_AMOUNT_CANDIDATES if c in opps),
        "opp_deadline_cols_present": sorted(c for c in OPP_DEADLINE_CANDIDATES if c in opps),
        "has_pages_table": "pages" in snapshot,
    }


def live_snapshot() -> dict:
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
            snapshot: dict[str, list[str]] = {}
            for table in TABLES:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s ORDER BY ordinal_position",
                    (table,),
                )
                cols = [r[0] for r in cur.fetchall()]
                if cols:
                    snapshot[table] = cols
    finally:
        conn.close()
    return {"tables": snapshot, "gaps": summarize_gaps(snapshot)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if not os.getenv("DB_PASSWORD"):
        print("Missing DB_PASSWORD in .env.")
        return 2
    data = live_snapshot()
    text = json.dumps(data, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}: tables={list(data['tables'])} gaps={data['gaps']}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
