# Devolved Funders Integration

Update, complete, then integrate Scottish (OSCR) + Northern Irish (CCNI) funder data from Azure into production.

## Sources of truth

| System | Location | Role |
|--------|----------|------|
| Staging Scot/NI | `psql-grantsai-db / grantseeker_devolved` | `source_register='scotland'/'northern_ireland'` — update + complete here |
| E&W prod | `grantseeker-pg-server / postgres` | `funders` has NO `source_register` — integrate here via migration |
| Corporate staging | `psql-grantsai-db / grantseeker_corporate` | Reference only |
| Code | `jimjcranshaw/oscr-integration` (`Scottish-Charities/`) | OSCR/CCNI pipelines, inserts, merge reference |
| Code | `jimjcranshaw/corporate-partnerships` | Prod-safety patterns, `merge_corporate_to_production.py` reference |
| Code | `jimjcranshaw/azure-production-v2` | Scrape engines on `grantseeker-vm` |

> Host + database travel as a pair. (`grantseeker-pg-server`, `postgres`) = prod. (`psql-grantsai-db`, `postgres`) = admin, no funders table.

## Lanes (each = 1 issue, 1 sub-agent, concurrent within waves)

* Wave 1 (read-only, parallel): #1 audit counts, #2 schema reconcile, #3 QA rubric baseline
* Wave 2 (staging writes, parallel after #1): #4 register refresh, #5 profile backfill, #6 postcode + trustee gap
* Wave 3 (prod, after #2+#4+#5): #7 merge devolved → prod with charity-number-scoped dedupe

## Guards

* Staging writes only in Wave 2. Prod DDL only in #7 behind `--dry-run` + `information_schema` checks.
* `ON CONFLICT (name)` is banned for cross-register merges — UK-wide grantmakers share names across registers. Use charity-number-scoped keys (`SC…`, `NIC…`, 7-digit E&W).
* Every lane must end with test evidence (`test_oscr_integration.py`, `test_corporate_integration.py`, or rubric scores).
