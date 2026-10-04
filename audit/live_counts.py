"""Live devolved counts (Issue #1) — single Azure account only.

Usage:
  python audit/live_counts.py            # uses .env DB_* -> grantseeker_devolved
  python audit/live_counts.py --out audit/counts.json

No MSCREDITS needed. Prod reads skipped by design until you say prod exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def get_devolved_conn():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "psql-grantsai-db.postgres.database.azure.com"),
        user=os.getenv("DB_USER", "grantsadmin"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DEVOLVED_DB_NAME", "grantseeker_devolved"),
        port=int(os.getenv("DB_PORT", "5432")),
        sslmode="require",
    )


def live_counts() -> dict:
    conn = get_devolved_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT source_register, COUNT(*) FROM funders GROUP BY source_register ORDER BY 1")
            by_register = [{"source_register": r[0], "funders": r[1]} for r in cur.fetchall()}
            cur.execute(
                "SELECT f.source_register, COUNT(*) FROM funders f "
                "WHERE EXISTS (SELECT 1 FROM funding_opportunities o WHERE o.funder_id = f.id) "
                "GROUP BY f.source_register"
            )
            with_opps = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM funding_opportunities")
            total_opps = cur.fetchone()[0]
    finally:
        conn.close()
    rows = [
        {
            "source_register": r["source_register"],
            "funders": r["funders"],
            "with_opps": with_opps.get(r["source_register"], 0),
        }
        for r in by_register
    ]
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "db": os.getenv("DEVOLVED_DB_NAME", "grantseeker_devolved"),
        "by_register": rows,
        "total_opportunities": total_opps,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if not os.getenv("DB_PASSWORD"):
        print("Missing DB_PASSWORD in .env — single Azure account password goes there.", file=sys.stderr)
        return 2
    data = live_counts()
    text = json.dumps(data, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
