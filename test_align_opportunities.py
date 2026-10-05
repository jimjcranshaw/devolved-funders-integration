"""Tests for integrate/align_opportunities.py pure helpers (no DB)."""

from integrate.align_opportunities import EW_TARGET_COLUMNS, migration_sql, missing_columns


def test_detects_all_nine_missing_on_old_schema():
    old = {"id", "funder_id", "funding_amounts", "deadlines", "description"}
    assert len(missing_columns(old)) == 9


def test_reconciled_schema_needs_nothing_but_index():
    live = {n for n, _ in EW_TARGET_COLUMNS} | {"id", "funding_amounts"}
    sql = migration_sql(live)
    assert "ADD COLUMN" not in sql
    assert "idx_opps_deadline" in sql


def test_sql_uses_if_not_exists_and_keeps_old_cols():
    sql = migration_sql(set())
    assert sql.count("IF NOT EXISTS") == 10  # 9 columns + index
    assert "funding_amounts" not in sql and "deadlines" not in sql.replace("deadline", "")
    assert "min_amount NUMERIC" in sql
    assert "excludes_political BOOLEAN DEFAULT false" in sql


def test_booleans_default_false():
    ddl = dict(EW_TARGET_COLUMNS)
    for col in ("excludes_individuals", "excludes_for_profit", "excludes_religious", "excludes_political"):
        assert ddl[col] == "BOOLEAN DEFAULT false"
