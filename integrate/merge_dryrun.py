"""Production-merge dry-run simulator (Issue #7) — PURE, no database, no network.

Simulates the devolved-funders -> production merge against in-memory fixture
dicts and prints what WOULD be inserted / updated / skipped. Applies nothing.

Hard constraints encoded here (see integrate/merge_plan.md for the runbook):
  (a) Production lives on a DIFFERENT, currently unreachable server
      (grantseeker-pg-server / postgres; the only functional account is
      staging-only). This script therefore never connects anywhere: there is
      no psycopg2 import, no socket, no credentials. Connection parameters
      for the future prod run are read from the environment (injected, never
      hardcoded) and used only to label output.
  (b) Production funders has NO source_register until migration. migration_sql()
      emits information_schema-guarded DDL plus the england_wales backfill.
  (c) Dedupe key is charity-number-scoped (SC/NIC/7-digit), NEVER name-only
      ON CONFLICT — UK-wide grantmakers share names across registers.
      Key logic is imported from audit.coverage.dedupe_key (single source).
  (d) Default mode is dry-run and applies nothing. --apply requires --confirm,
      and even then only emits the ordered SQL/apply checklist for DBA
      review — this script has no driver and executes nothing.
  (e) Every migrated opportunity keeps opportunity_source provenance and the
      new E&W columns (min/max_amount, deadline, eligibility_criteria,
      beneficiary_groups, excludes_*).

Usage:
  python integrate/merge_dryrun.py                      # dry-run on fixtures
  python integrate/merge_dryrun.py --apply --confirm     # emit apply checklist
  python integrate/merge_dryrun.py --apply               # refused (exit 2)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.coverage import dedupe_key  # noqa: E402  (number-scoped key, single source)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Columns the prod migration must guarantee before any data moves.
# Mirrors integrate/schema_check.py DESIRED_COLUMNS + align_opportunities.py
# EW_TARGET_COLUMNS (single source for DDL text lives in those modules; the
# names are repeated here only for dry-run reporting, never executed).
REQUIRED_FUNDER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_register", "VARCHAR(20) DEFAULT 'england_wales'"),
    ("oscr_purposes", "TEXT"),
    ("constitutional_form", "VARCHAR(100)"),
    ("cross_border_registered", "BOOLEAN DEFAULT FALSE"),
)

REQUIRED_OPP_COLUMNS: tuple[tuple[str, str], ...] = (
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

# Funder fields an UPDATE may fill, and only when the prod value is blank.
# charity_number and name are identity — never overwritten. source_register on
# an existing prod row is never overwritten either (an E&W row matched by a
# devolved number keeps 'england_wales'; overlap is flagged via
# cross_border_registered instead).
FUNDER_FILL_FIELDS: tuple[str, ...] = (
    "website",
    "description",
    "oscr_purposes",
    "constitutional_form",
)

# Every migrated opportunity must carry these through (provenance + E&W set).
OPP_CARRY_FIELDS: tuple[str, ...] = (
    "opportunity_source",
    "min_amount",
    "max_amount",
    "deadline",
    "eligibility_criteria",
    "beneficiary_groups",
    "excludes_individuals",
    "excludes_for_profit",
    "excludes_religious",
    "excludes_political",
)

# Env names for the future prod run. No defaults, no hardcoded secrets: the
# values only label output; nothing ever dials out.
DB_ENV_VARS: tuple[str, ...] = (
    "PROD_DB_HOST",
    "PROD_DB_NAME",
    "PROD_DB_USER",
    "PROD_DB_PASSWORD",
)


# ---------------------------------------------------------------------------
# Fixture data (demo + test input — dicts in, plan out)
# ---------------------------------------------------------------------------

SAMPLE_PROD_FUNDERS: list[dict] = [
    # Pre-existing E&W row sharing a name with a devolved funder, but a
    # DIFFERENT register/number: must NOT be merged by name.
    {
        "id": 101,
        "name": "Garfield Weston Foundation",
        "charity_number": "207656",
        "source_register": "england_wales",
        "website": "https://garfieldweston.org",
        "description": "E&W grantmaker profile.",
        "oscr_purposes": None,
        "constitutional_form": None,
    },
    # Fully-populated E&W row whose devolved twin is already merged.
    {
        "id": 102,
        "name": "Robertson Trust",
        "charity_number": "SC002970",
        "source_register": "scotland",
        "website": "https://therobertsontrust.org.uk",
        "description": "Scotland's largest independent funder.",
        "oscr_purposes": ["advancement of education"],
        "constitutional_form": "Trust",
    },
    # Real E&W row that a staging placeholder stub must never touch.
    {
        "id": 103,
        "name": "Gannochy Trust",
        "charity_number": "SC003133",
        "source_register": "scotland",
        "website": "https://gannochytrust.org.uk",
        "description": "Real merged profile with website and programmes.",
        "oscr_purposes": None,
        "constitutional_form": None,
    },
]

SAMPLE_STAGING_FUNDERS: list[dict] = [
    # Same NAME as prod id 101 but SC number -> distinct key -> INSERT.
    {
        "id": 1,
        "name": "Garfield Weston Foundation",
        "charity_number": "SC038043",
        "source_register": "scotland",
        "website": "https://garfieldweston.org",
        "description": "Scottish-registered arm profile.",
        "oscr_purposes": ["advancement of education"],
        "constitutional_form": "Trust",
    },
    # Exact twin of prod id 102 -> SKIP (already present, nothing to fill).
    {
        "id": 2,
        "name": "Robertson Trust",
        "charity_number": "SC002970",
        "source_register": "scotland",
        "website": "https://therobertsontrust.org.uk",
        "description": "Scotland's largest independent funder.",
        "oscr_purposes": ["advancement of education"],
        "constitutional_form": "Trust",
    },
    # Placeholder stub: no charity number, name collides with prod id 103.
    # Must SKIP — never update the real row by name.
    {
        "id": 3,
        "name": "Gannochy Trust",
        "charity_number": "",
        "source_register": "scotland",
        "website": "",
        "description": "",
        "oscr_purposes": None,
        "constitutional_form": None,
    },
    # Genuinely new NI funder -> INSERT.
    {
        "id": 4,
        "name": "Community Foundation NI",
        "charity_number": "NIC101182",
        "source_register": "northern_ireland",
        "website": "https://communityfoundationni.org",
        "description": "NI community funder.",
        "oscr_purposes": None,
        "constitutional_form": "Company",
    },
]

SAMPLE_STAGING_OPPS: list[dict] = [
    {
        "id": 11,
        "staging_funder_id": 1,
        "opportunity_title": "Main Grants Programme",
        "description": "Revenue and capital grants.",
        "opportunity_source": "oscr-scrape",
        "min_amount": 1000,
        "max_amount": 100000,
        "deadline": "2026-12-31",
        "eligibility_criteria": "Registered charities in Scotland.",
        "beneficiary_groups": "children, older people",
        "excludes_individuals": True,
        "excludes_for_profit": True,
        "excludes_religious": False,
        "excludes_political": True,
    },
]

# (funder_key, normalised title) pairs already in prod for the demo.
SAMPLE_EXISTING_OPP_KEYS: set[tuple[tuple[str, str], str]] = set()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def is_placeholder(funder: dict) -> bool:
    """True when a staging row is a stub that must never overwrite real data.

    A row is a placeholder when it carries no usable charity number (so it
    cannot form a number-scoped dedupe key) or is explicitly flagged as one.
    """
    if funder.get("is_placeholder"):
        return True
    num = (funder.get("charity_number") or "").strip()
    return num == ""


def funder_key(funder: dict) -> tuple[str, str] | None:
    """Number-scoped dedupe key, or None when the row cannot form one."""
    if is_placeholder(funder):
        return None
    key = dedupe_key(funder.get("charity_number") or "", funder.get("name") or "")
    if key[0] == "england_wales" and not (funder.get("charity_number") or "").strip():
        return None  # name fallback is not a merge key — never use it
    return key


def _norm_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def opp_key(funder_dedupe_key: tuple[str, str], opp: dict) -> tuple[tuple[str, str], str]:
    return (funder_dedupe_key, _norm_title(opp.get("opportunity_title") or ""))


def resolve_db_params(env: dict[str, str] | None = None) -> dict[str, str]:
    """Read prod connection params from the environment (injected, not coded).

    Returns whatever is set (possibly empty) — used only to label output.
    Nothing in this module connects anywhere.
    """
    src = env if env is not None else os.environ
    return {var: src.get(var, "") for var in DB_ENV_VARS}


# ---------------------------------------------------------------------------
# Pure planners: fixture dicts in, plan out
# ---------------------------------------------------------------------------

def plan_funder_merge(
    staging_funders: list[dict],
    prod_funders: list[dict],
) -> dict[str, list[dict]]:
    """Simulate the funder merge. Returns inserts/updates/skips/collisions.

    Rules (all number-scoped, never name-matched):
      * staging row without a number key      -> skip (manual review)
      * staging placeholder                   -> skip, even on name collision
      * key absent from prod                  -> insert
      * key present, staging fills blank prod  -> update (fill-only, listed)
      * key present, nothing to fill           -> skip (already present)
    """
    prod_by_key: dict[tuple[str, str], dict] = {}
    for prow in prod_funders:
        pnum = (prow.get("charity_number") or "").strip()
        if not pnum:
            continue  # prod rows without numbers are invisible to the merge
        prod_by_key[dedupe_key(pnum, prow.get("name") or "")] = prow

    plan: dict[str, list[dict]] = {
        "inserts": [],
        "updates": [],
        "skips": [],
        "collisions": [],
    }

    names_to_keys: dict[str, set[tuple[str, str]]] = {}
    for row in list(staging_funders) + list(prod_funders):
        num = (row.get("charity_number") or "").strip()
        if not num:
            continue
        names_to_keys.setdefault(row.get("name", "").strip().lower(), set()).add(
            dedupe_key(num, row.get("name") or "")
        )
    for name, keys in sorted(names_to_keys.items()):
        if len(keys) > 1:
            plan["collisions"].append(
                {
                    "name": name,
                    "keys": sorted(keys),
                    "resolution": "separate rows per number-scoped key; no name merge",
                }
            )

    for srow in staging_funders:
        label = f"{srow.get('name')} [{srow.get('charity_number') or 'no-number'}]"
        if is_placeholder(srow):
            plan["skips"].append(
                {
                    "staging_id": srow.get("id"),
                    "funder": label,
                    "action": "skip",
                    "reason": "placeholder row (no charity number): "
                    "never overwrites real rows; needs manual review",
                }
            )
            continue
        key = funder_key(srow)
        if key is None:
            plan["skips"].append(
                {
                    "staging_id": srow.get("id"),
                    "funder": label,
                    "action": "skip",
                    "reason": "no number-scoped key: excluded from auto-merge, "
                    "queue for manual review",
                }
            )
            continue
        existing = prod_by_key.get(key)
        if existing is None:
            plan["inserts"].append(
                {
                    "staging_id": srow.get("id"),
                    "funder": label,
                    "action": "insert",
                    "key": list(key),
                    "source_register": srow.get("source_register"),
                }
            )
            continue
        would_set = {
            f: srow.get(f)
            for f in FUNDER_FILL_FIELDS
            if not _blank(srow.get(f)) and _blank(existing.get(f))
        }
        if would_set:
            plan["updates"].append(
                {
                    "staging_id": srow.get("id"),
                    "prod_id": existing.get("id"),
                    "funder": label,
                    "action": "update",
                    "key": list(key),
                    "would_set": would_set,
                    "note": "fill blank prod fields only; populated prod "
                    "values, charity_number, name and source_register untouched",
                }
            )
        else:
            plan["skips"].append(
                {
                    "staging_id": srow.get("id"),
                    "prod_id": existing.get("id"),
                    "funder": label,
                    "action": "skip",
                    "reason": "already present under the same number-scoped "
                    "key with nothing left to fill",
                }
            )
    return plan


def plan_opportunity_merge(
    staging_opps: list[dict],
    staging_funders: list[dict],
    funder_plan: dict[str, list[dict]],
    existing_opp_keys: set[tuple[tuple[str, str], str]],
) -> dict[str, list[dict]]:
    """Simulate the opportunity merge, preserving provenance + E&W columns.

    An opp follows its funder: funders that insert/update map to a prod
    funder; funders that skip leave their opps skipped (no orphan rows).
    """
    by_staging_id = {f.get("id"): f for f in staging_funders}
    insert_ids = {e["staging_id"] for e in funder_plan["inserts"]}
    update_prod = {e["staging_id"]: e["prod_id"] for e in funder_plan["updates"]}

    plan: dict[str, list[dict]] = {"inserts": [], "skips": []}
    for opp in staging_opps:
        sf = by_staging_id.get(opp.get("staging_funder_id"), {})
        fkey = funder_key(sf)
        olabel = f"{opp.get('opportunity_title')} (funder: {sf.get('name')})"
        if fkey is None:
            plan["skips"].append(
                {
                    "staging_opp_id": opp.get("id"),
                    "opportunity": olabel,
                    "action": "skip",
                    "reason": "owning funder skipped (placeholder or keyless): "
                    "no orphan opportunity rows",
                }
            )
            continue
        if opp.get("staging_funder_id") not in insert_ids and opp.get(
            "staging_funder_id"
        ) not in update_prod:
            plan["skips"].append(
                {
                    "staging_opp_id": opp.get("id"),
                    "opportunity": olabel,
                    "action": "skip",
                    "reason": "owning funder already present with nothing to "
                    "fill; opp handled by existing prod row review",
                }
            )
            continue
        okey = opp_key(fkey, opp)
        if okey in existing_opp_keys:
            plan["skips"].append(
                {
                    "staging_opp_id": opp.get("id"),
                    "opportunity": olabel,
                    "action": "skip",
                    "reason": "same (funder key, title) already in prod",
                }
            )
            continue
        carried = {f: opp.get(f) for f in OPP_CARRY_FIELDS}
        plan["inserts"].append(
            {
                "staging_opp_id": opp.get("id"),
                "opportunity": olabel,
                "action": "insert",
                "funder_key": list(fkey),
                "carried_fields": carried,
            }
        )
    return plan


def migration_sql(prod_funder_columns: set[str], prod_opp_columns: set[str]) -> str:
    """Render information_schema-guarded migration SQL. Text only, not run."""
    have_f = {c.lower() for c in prod_funder_columns}
    have_o = {c.lower() for c in prod_opp_columns}
    blocks: list[str] = []
    for name, ddl in REQUIRED_FUNDER_COLUMNS:
        if name.lower() in have_f:
            blocks.append(f"-- already present: funders.{name} (no-op)")
            continue
        blocks.append(
            "DO $$\n"
            "BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM information_schema.columns "
            f"WHERE table_name = 'funders' AND column_name = '{name}') THEN\n"
            f"    ALTER TABLE funders ADD COLUMN {name} {ddl};\n"
            "  END IF;\n"
            "END $$;"
        )
    blocks.append(
        "-- backfill pre-merge prod rows (E&W) once source_register exists\n"
        "UPDATE funders SET source_register = 'england_wales' "
        "WHERE source_register IS NULL;"
    )
    blocks.append(
        "CREATE INDEX IF NOT EXISTS idx_funders_source_register "
        "ON funders(source_register);"
    )
    for name, ddl in REQUIRED_OPP_COLUMNS:
        if name.lower() in have_o:
            blocks.append(f"-- already present: funding_opportunities.{name} (no-op)")
            continue
        blocks.append(
            "DO $$\n"
            "BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM information_schema.columns "
            f"WHERE table_name = 'funding_opportunities' "
            f"AND column_name = '{name}') THEN\n"
            f"    ALTER TABLE funding_opportunities ADD COLUMN {name} {ddl};\n"
            "  END IF;\n"
            "END $$;"
        )
    return "\n".join(blocks)


def render_report(
    funder_plan: dict[str, list[dict]],
    opp_plan: dict[str, list[dict]],
    target_label: str,
) -> str:
    """Human-readable dry-run report of what WOULD happen."""
    lines = [
        "=" * 70,
        "MERGE DRY-RUN (simulation only — nothing applied)",
        f"target: {target_label}",
        "=" * 70,
    ]
    fi, fu, fs = (len(funder_plan[k]) for k in ("inserts", "updates", "skips"))
    oi, osk = len(opp_plan["inserts"]), len(opp_plan["skips"])
    lines.append(f"funders: {fi} insert(s), {fu} update(s), {fs} skip(s)")
    for e in funder_plan["inserts"]:
        lines.append(f"  INSERT {e['funder']} key={e['key']} [{e['source_register']}]")
    for e in funder_plan["updates"]:
        lines.append(
            f"  UPDATE prod_id={e['prod_id']} {e['funder']} "
            f"fill={sorted(e['would_set'])}"
        )
    for e in funder_plan["skips"]:
        lines.append(f"  SKIP   {e['funder']} — {e['reason']}")
    if funder_plan["collisions"]:
        lines.append("cross-register name collisions (resolved by number key):")
        for c in funder_plan["collisions"]:
            lines.append(f"  {c['name']!r} -> {c['keys']} ({c['resolution']})")
    lines.append(f"opportunities: {oi} insert(s), {osk} skip(s)")
    for e in opp_plan["inserts"]:
        kept = [k for k, v in e["carried_fields"].items() if not _blank(v)]
        lines.append(f"  INSERT {e['opportunity']} carries={sorted(kept)}")
    for e in opp_plan["skips"]:
        lines.append(f"  SKIP   {e['opportunity']} — {e['reason']}")
    lines.append("=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (reporting only — no driver, no connections)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Simulate the devolved -> prod merge.")
    ap.add_argument("--dry-run", action="store_true", help="print the plan (default)")
    ap.add_argument("--apply", action="store_true", help="requires --confirm; "
                    "emits the apply checklist/SQL for review, executes nothing")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--emit-sql", default="", help="write migration SQL to this path")
    args = ap.parse_args(argv)

    if args.apply and not args.confirm:
        print("Refusing: --apply needs --confirm. Nothing planned, nothing applied.")
        return 2

    params = resolve_db_params()
    target = (
        f"{params.get('PROD_DB_HOST') or '<PROD_DB_HOST unset>'}/"
        f"{params.get('PROD_DB_NAME') or '<PROD_DB_NAME unset>'} (from env; unreachable)"
    )

    funder_plan = plan_funder_merge(SAMPLE_STAGING_FUNDERS, SAMPLE_PROD_FUNDERS)
    opp_plan = plan_opportunity_merge(
        SAMPLE_STAGING_OPPS, SAMPLE_STAGING_FUNDERS, funder_plan, SAMPLE_EXISTING_OPP_KEYS
    )
    print(render_report(funder_plan, opp_plan, target))

    # Prod-schema-copy column sets: staging snapshot HAS the new columns, real
    # prod funders LACK source_register — show the guarded migration for that.
    sql = migration_sql(
        {"id", "name", "website", "description", "charity_number"},
        {"id", "funder_id", "opportunity_title", "description", "opportunity_source",
         "funding_amounts", "deadlines"},
    )
    if args.apply:  # --confirm given: emit checklist + SQL text, execute nothing
        print("APPLY CHECKLIST (ordered; to be executed on prod-access day):")
        print("  1. snapshot prod funders/opps (pre-merge backup tables)")
        print("  2. run the information_schema-guarded migration below")
        print("  3. insert funders per plan, then fill-only updates")
        print("  4. insert opportunities with provenance + E&W columns")
        print("  5. run per-step verify queries from integrate/merge_plan.md")
        print("--- migration SQL (review only) ---")
        print(sql)
        print("--- end SQL (NOT executed: no driver, no connection) ---")
        if args.emit_sql:
            Path(args.emit_sql).write_text(sql + "\n", encoding="utf-8")
            print(f"wrote {args.emit_sql}")
        return 0

    print("dry-run complete: no connections opened, nothing applied. "
          "Re-run with --apply --confirm to emit the apply checklist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
