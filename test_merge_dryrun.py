"""Tests for integrate/merge_dryrun.py (Issue #7). Pure simulation — no DB.

Run: python -m pytest test_merge_dryrun.py -v
"""

import copy

from integrate.merge_dryrun import (
    SAMPLE_PROD_FUNDERS,
    SAMPLE_STAGING_FUNDERS,
    SAMPLE_STAGING_OPPS,
    funder_key,
    is_placeholder,
    main,
    migration_sql,
    opp_key,
    plan_funder_merge,
    plan_opportunity_merge,
    resolve_db_params,
)


def _fresh():
    return copy.deepcopy(SAMPLE_STAGING_FUNDERS), copy.deepcopy(SAMPLE_PROD_FUNDERS)


def test_cross_register_name_collision_resolved_by_number_key():
    """Same name in two registers -> INSERT as a separate row, never merged."""
    staging, prod = _fresh()
    plan = plan_funder_merge(staging, prod)
    garfield = [e for e in plan["inserts"] if e["staging_id"] == 1]
    assert len(garfield) == 1
    assert garfield[0]["key"] == ["scotland", "SC038043"]
    # The E&W namesake must be untouched: no update targets prod id 101.
    assert all(e.get("prod_id") != 101 for e in plan["updates"])
    # Collision is reported explicitly.
    assert any(
        "garfield weston foundation" in c["name"] and len(c["keys"]) > 1
        for c in plan["collisions"]
    )


def test_already_present_rows_skipped_not_duplicated():
    """Twin of a merged row -> SKIP, with nothing inserted or updated."""
    staging, prod = _fresh()
    plan = plan_funder_merge(staging, prod)
    dup = [e for e in plan["skips"] if e.get("staging_id") == 2]
    assert len(dup) == 1
    assert "already present" in dup[0]["reason"]
    assert not [e for e in plan["inserts"] if e["staging_id"] == 2]
    assert not [e for e in plan["updates"] if e["staging_id"] == 2]


def test_placeholder_rows_never_overwrite_real_rows():
    """Numberless stub sharing a real row's name -> SKIP, real row untouched."""
    staging, prod = _fresh()
    stub = next(f for f in staging if f["id"] == 3)
    assert is_placeholder(stub)
    assert funder_key(stub) is None  # placeholders cannot form a merge key
    plan = plan_funder_merge(staging, prod)
    skipped = [e for e in plan["skips"] if e.get("staging_id") == 3]
    assert len(skipped) == 1
    assert "placeholder" in skipped[0]["reason"]
    assert not [e for e in plan["updates"] if e.get("prod_id") == 103]
    assert not [e for e in plan["inserts"] if e["staging_id"] == 3]


def test_update_fills_blanks_only():
    staging, prod = _fresh()
    prod_gannochy = next(p for p in prod if p["id"] == 103)
    prod_gannochy["oscr_purposes"] = None  # blank slot the staging row can fill
    staging.append(
        {
            "id": 9,
            "name": "Gannochy Trust",
            "charity_number": "SC003133",
            "source_register": "scotland",
            "website": "https://evil-overwrite.example",
            "description": "",
            "oscr_purposes": ["advancement of education"],
            "constitutional_form": None,
        }
    )
    plan = plan_funder_merge(staging, prod)
    upd = [e for e in plan["updates"] if e["staging_id"] == 9]
    assert len(upd) == 1
    assert upd[0]["would_set"] == {"oscr_purposes": ["advancement of education"]}
    # Populated prod website survives; blank staging description fills nothing.
    assert "website" not in upd[0]["would_set"]


def test_opportunity_keeps_provenance_and_ew_columns():
    staging, prod = _fresh()
    fplan = plan_funder_merge(staging, prod)
    oplan = plan_opportunity_merge(
        copy.deepcopy(SAMPLE_STAGING_OPPS), staging, fplan, set()
    )
    assert len(oplan["inserts"]) == 1
    carried = oplan["inserts"][0]["carried_fields"]
    assert carried["opportunity_source"] == "oscr-scrape"
    for col in ("min_amount", "max_amount", "deadline", "eligibility_criteria",
                "beneficiary_groups", "excludes_individuals",
                "excludes_for_profit", "excludes_religious", "excludes_political"):
        assert col in carried
    assert carried["min_amount"] == 1000


def test_opportunity_of_skipped_funder_is_skipped():
    """Orphan guard: placeholder funder's opps never insert alone."""
    staging, prod = _fresh()
    fplan = plan_funder_merge(staging, prod)
    opps = [
        {
            "id": 99,
            "staging_funder_id": 3,  # the placeholder stub
            "opportunity_title": "Ghost Programme",
            "opportunity_source": "oscr-scrape",
        }
    ]
    oplan = plan_opportunity_merge(opps, staging, fplan, set())
    assert oplan["inserts"] == []
    assert len(oplan["skips"]) == 1


def test_existing_opportunity_skipped():
    staging, prod = _fresh()
    fplan = plan_funder_merge(staging, prod)
    fkey = ("scotland", "SC038043")
    existing = {opp_key(fkey, {"opportunity_title": "Main Grants Programme"})}
    oplan = plan_opportunity_merge(
        copy.deepcopy(SAMPLE_STAGING_OPPS), staging, fplan, existing
    )
    assert oplan["inserts"] == []
    assert len(oplan["skips"]) == 1


def test_migration_sql_guarded_with_backfill():
    sql = migration_sql({"id", "name"}, {"id", "opportunity_title"})
    assert "information_schema.columns" in sql
    assert "source_register" in sql
    assert "england_wales" in sql  # backfill for pre-merge prod rows
    assert "idx_funders_source_register" in sql
    assert "min_amount" in sql
    # Already-present columns render as no-op comments, not ALTERs.
    sql2 = migration_sql(
        {"id", "name", "source_register", "oscr_purposes",
         "constitutional_form", "cross_border_registered"},
        {"id", "opportunity_title", "min_amount", "max_amount", "deadline",
         "eligibility_criteria", "beneficiary_groups", "excludes_individuals",
         "excludes_for_profit", "excludes_religious", "excludes_political",
         "opportunity_source"},
    )
    assert "ADD COLUMN" not in sql2


def test_db_params_injected_never_hardcoded():
    params = resolve_db_params(env={})
    assert params == {
        "PROD_DB_HOST": "",
        "PROD_DB_NAME": "",
        "PROD_DB_USER": "",
        "PROD_DB_PASSWORD": "",
    }
    params = resolve_db_params(env={
        "PROD_DB_HOST": "h", "PROD_DB_NAME": "d",
        "PROD_DB_USER": "u", "PROD_DB_PASSWORD": "p",
    })
    assert params["PROD_DB_HOST"] == "h"
    assert params["PROD_DB_PASSWORD"] == "p"


def test_apply_requires_confirm(capsys):
    assert main(["--apply"]) == 2
    assert main([]) == 0  # default dry-run prints plan, applies nothing
    assert "INSERT" in capsys.readouterr().out
    assert main(["--apply", "--confirm"]) == 0


def test_no_db_driver_imported():
    import integrate.merge_dryrun as m

    assert not hasattr(m, "psycopg2")
    assert "psycopg2" not in dir(m)
