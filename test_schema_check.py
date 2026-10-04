"""Tests for integrate/schema_check.py (Issue #2)."""

from integrate.schema_check import guarded_migration_sql, missing_columns


def test_missing_detects_source_register():
    assert [n for n, _ in missing_columns({"id", "name"})] == ["source_register", "oscr_purposes"]


def test_reconciled_prod_still_emits_index():
    sql = guarded_migration_sql({"id", "source_register", "oscr_purposes"})
    assert "ADD COLUMN" not in sql
    assert "idx_funders_source_register" in sql


def test_guarded_alters_use_information_schema():
    sql = guarded_migration_sql({"id"})
    assert sql.count("information_schema.columns") == 2
    assert "ALTER TABLE funders ADD COLUMN source_register" in sql
