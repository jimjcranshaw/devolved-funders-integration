# Issue #7 — Production-merge runbook: devolved funders → prod

Design document for the day prod access returns. Companion simulator:
`integrate/merge_dryrun.py` (pure, fixture-based) + `test_merge_dryrun.py`.
The reference we improve on is
`oscr-integration/Scottish-Charities/merge_scotland_to_production.py`
(information_schema-guarded migration pattern kept; its **name-based**
funder matching deliberately replaced — see Step 4).

Scale on merge day: staging `grantseeker_devolved` holds **~5,755 funders /
~3,900 opportunities** ready to merge upward.

## 0. Preconditions (all must hold before Step 1)

- [ ] Waves 1–2 done: audit counts (#1), schema reconcile (#2), register
      refresh (#4), profile backfill (#5), postcode/trustee gaps (#6).
- [ ] Prod credentials restored for `grantseeker-pg-server / postgres`.
      The single functional account today is **staging-only** — never assume
      the prod server is reachable until this box is ticked.
- [ ] A **prod-schema copy** is staged on reachable Postgres (see Step 1);
      the simulator is green: `python -m pytest test_merge_dryrun.py`.
- [ ] `PROD_DB_HOST / PROD_DB_NAME / PROD_DB_USER / PROD_DB_PASSWORD` are
      exported in the operator shell. No credential is ever hardcoded or
      committed; nothing connects without them, and the dry-run needs none.
- [ ] Maintenance window agreed; pre-merge backup tables created (Step 2).

## Ordered steps

### Step 1 — Stage a prod-schema copy where we can reach it

Restore the latest prod backup onto staging Postgres as e.g.
`grantseeker_prodcopy`, then run the simulator's migration SQL against the
**copy** first. All connection values come from the environment.

> Verify: `SELECT count(*) FROM funders;` on the copy matches the last
> known prod count, and
> `SELECT column_name FROM information_schema.columns WHERE table_name='funders';`
> shows **no** `source_register`.

> Rollback: n/a (read-only restore). Drop and re-restore the copy on mismatch.

### Step 2 — Guarded migration on the copy, then (on prod day) on prod

Apply `migration_sql()` output in order:

1. `information_schema`-guarded `ALTER TABLE funders ADD COLUMN`
   `source_register`, `oscr_purposes`, `constitutional_form`,
   `cross_border_registered` (each a no-op if already present).
2. Backfill `UPDATE funders SET source_register='england_wales' WHERE
   source_register IS NULL;` — every pre-merge prod row is E&W.
3. `CREATE INDEX IF NOT EXISTS idx_funders_source_register ...`.
4. Guarded adds of the E&W opportunity columns (`min_amount`,
   `max_amount`, `deadline`, `eligibility_criteria`, `beneficiary_groups`,
   `excludes_individuals/for_profit/religious/political`) — additive only,
   existing `funding_amounts`/`deadlines` columns are kept.

> Verify (per column, e.g.):
> `SELECT column_name, data_type FROM information_schema.columns WHERE table_name='funders' AND column_name='source_register';`
> and `SELECT count(*) FROM funders WHERE source_register IS NULL;` → **0**.
> For opps: `SELECT count(*) FROM information_schema.columns WHERE table_name='funding_opportunities' AND column_name IN ('min_amount','max_amount','deadline','eligibility_criteria','beneficiary_groups','excludes_individuals','excludes_for_profit','excludes_religious','excludes_political');` → **9**.

> Rollback: DDL has no transactional undo across the window, so snapshot
> first: `CREATE TABLE funders_premerge_<YYYYMMDD> AS TABLE funders;`
> (same for `funding_opportunities`). To undo, drop added columns or
> restore from the snapshot tables, then drop the snapshots when signed off.

### Step 3 — Dry-run the data plan (no writes, runnable today)

`python integrate/merge_dryrun.py` prints planned inserts / updates / skips
against fixtures; on merge day the same planners run against exports from
`grantseeker_devolved` (CSV in, plan out — still no live writes).

> Verify: insert count + skip count + update count reconcile to the staging
> export row count; every `collisions` entry names a genuine cross-register
> pair; zero `update` rows target E&W `source_register` rows by name.

> Rollback: n/a — prints only.

### Step 4 — Merge funders by charity-number-scoped key (prod day, manual)

For each staging funder, compute `dedupe_key(charity_number, name)`
(`audit/coverage.py`: `SC…`→scotland, `NIC…`→northern_ireland, 7-digit→
england_wales) and:

- key absent from prod → **INSERT** with its `source_register`;
- key present → **UPDATE fill-only**: set only whitelisted fields
  (`website`, `description`, `oscr_purposes`, `constitutional_form`) where
  prod is blank; never touch `name`, `charity_number`, `source_register`;
  set `cross_border_registered=TRUE` where the same organisation spans
  registers (this replaces the reference script's `WHERE name = %s` merge,
  which would collapse distinct UK-wide grantmakers sharing a name);
- key present with nothing to fill → **SKIP** (already present);
- no usable number (placeholder stubs) → **SKIP** to a manual-review queue —
  placeholders never overwrite real rows, even on exact name match.

> Verify: `SELECT source_register, count(*) FROM funders GROUP BY 1;`
> shows scotland/NI counts ≈ staging exports; spot-check
> `SELECT * FROM funders WHERE name='Garfield Weston Foundation';`
> returns **two** rows (E&W + SC numbers intact).

> Rollback: `DELETE FROM funders WHERE source_register IN
> ('scotland','northern_ireland') AND created_at >= '<merge_start>';`
> then restore any fill-updates from `funders_premerge_<date>` by `id`.
> (`<merge_start>` is captured with `SELECT clock_timestamp();` pre-merge.)

### Step 5 — Merge opportunities with provenance (prod day, manual)

Insert each staging opportunity under its remapped prod `funder_id`,
carrying `opportunity_source` plus all nine E&W columns. Opp identity is
`(funder dedupe key, normalised title)` — already-present pairs SKIP.
Opportunities whose funder SKIPPED stay out (no orphans).

> Verify: `SELECT opportunity_source, count(*) FROM funding_opportunities
> WHERE created_at >= '<merge_start>' GROUP BY 1;` — every new row has a
> non-null source; `SELECT count(*) FROM funding_opportunities WHERE
> funder_id NOT IN (SELECT id FROM funders);` → **0**.

> Rollback: `DELETE FROM funding_opportunities WHERE created_at >=
> '<merge_start>';` or restore from `funding_opportunities_premerge_<date>`.

### Step 6 — Post-merge sign-off

Re-run QA rubric baseline (#3) deltas, confirm counts, drop pre-merge
snapshots only after sign-off.

> Verify: total `funders` = pre-merge prod + Step-4 inserts;
> `SELECT count(*) FROM funders WHERE source_register IS NULL;` → **0**.

> Rollback: full restore from the Step-2 snapshot tables.

## What remains manual (not in this design)

- The actual prod run itself (Steps 2 prod leg, 4, 5, 6) — requires the
  restored `grantseeker-pg-server` credentials, which must never be
  invented, hardcoded, or committed.
- Triage of the manual-review queue (keyless/placeholder rows).
- Backfilling the new E&W opportunity columns from free-text
  `funding_amounts`/`deadlines` (DeepSeek parsing lane, separate issue).
- `chunk_embeddings` vectors stay out of scope per `AI_POLICY.md`.
