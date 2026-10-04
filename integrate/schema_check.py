"""Schema-diff helper (Issue #2) — read-only SQL generation, never executes.

Cross-links: oscr-integration/merge_scotland_to_production.py:56-96
(information_schema guard pattern), corporate-partnerships/config.py:173
(prod funders has no source_register).
"""

from __future__ import annotations

# Desired prod columns for devolved merge: (name, ddl fragment).
DESIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_register", "VARCHAR(20) DEFAULT 'england_wales'"),
    ("oscr_purposes", "TEXT"),
)


def missing_columns(prod_columns: set[str]) -> list[tuple[str, str]]:
    """Return DESIRED_COLUMNS entries absent from the live prod column set."""
    have = {c.lower() for c in prod_columns}
    return [(name, ddl) for name, ddl in DESIRED_COLUMNS if name.lower() not in have]


def guarded_migration_sql(prod_columns: set[str]) -> str:
    """Generate information_schema-guarded ALTERs for missing columns only.

    Empty string when prod is already reconciled — applying nothing is the
    correct outcome, not an error.
    """
    lines = []
    for name, ddl in missing_columns(prod_columns):
        lines.append(
            "DO $$\n"
            "BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM information_schema.columns "
            f"WHERE table_name='funders' AND column_name='{name}') THEN\n"
            f"    ALTER TABLE funders ADD COLUMN {name} {ddl};\n"
            "  END IF;\n"
            "END $$;"
        )
    lines.append(
        "CREATE INDEX IF NOT EXISTS idx_funders_source_register "
        "ON funders(source_register);"
    )
    return "\n".join(lines)
