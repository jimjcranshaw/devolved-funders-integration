"""Align devolved funding_opportunities to the E&W column set (Issue #2 follow-up).

E&W target = live-prod columns per the qa-azure-funders rubric SQL (the only
authoritative record of prod; v3 README DDL predates the prod migration and
code search shows no in-repo migration adds these columns):

  min_amount, max_amount, deadline, eligibility_criteria,
  beneficiary_groups, excludes_individuals, excludes_for_profit,
  excludes_religious, excludes_political

Existing devolved columns (funding_amounts, deadlines, ...) are KEPT — this
migration is purely additive. Backfilling new columns from free-text
funding_amounts/deadlines is a separate DeepSeek parsing step, not this.

Usage:
  python integrate/align_opportunities.py --print-sql        # review only
  python integrate/align_opportunities.py --apply --confirm  # staging write
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

# name -> DDL fragment. Nullable on purpose: backfill comes later.
EW_TARGET_COLUMNS: tuple[tuple[str, str], ...] = (
    ("min_amount", "NUMERIC"),
    ("max_amount", "NUMERIC"),
    ("deadline", "TEXT"),
    ("eligibility_criteria", "TEXT"),
    ("beneficiary_groups", "TEXT"),
    ("excludes_individuals", "BOOLEAN DEFAULT false"),
    ("excludes_for_profit", "BOOLEAN DEFAULT false"),
    ("excludes_religious", "BOOLEAN DEFAULT false"),
    ("excludes_political", "BOOLEAN DEFAULT false"),
)


def missing_columns(live_columns: set[str]) -> list[tuple[str, str]]:
    have = {c.lower() for c in live_columns}
    return [(n, ddl) for n, ddl in EW_TARGET_COLUMNS if n.lower() not in have]


def migration_sql(live_columns: set[str]) -> str:
    stmts = [
        f"ALTER TABLE funding_opportunities ADD COLUMN IF NOT EXISTS {name} {ddl};"
        for name, ddl in missing_columns(live_columns)
    ]
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_opps_deadline ON funding_opportunities(deadline);"
    )
    return "\n".join(stmts)


def live_columns() -> set[str]:
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
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'funding_opportunities'"
            )
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-sql", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    if not os.getenv("DB_PASSWORD"):
        print("Missing DB_PASSWORD in .env.")
        return 2
    cols = live_columns()
    sql = migration_sql(cols)
    if args.apply:
        if not args.confirm:
            print("Refusing: --apply needs --confirm (staging write).")
            return 2
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
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
        print(f"Applied to {os.getenv('DEVOLVED_DB_NAME', 'grantseeker_devolved')}.")
        return 0
    print(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
