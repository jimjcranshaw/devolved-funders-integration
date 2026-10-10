# Thin-site triage — DEFAULT_TEMPLATE-only funders

## Provenance (read first)

* **Staging was unreachable from this host on 2026-10-10** (TCP SYN-drop on
  `psql-grantsai-db:5432`, repeated over ~30 min; HTTPS egress fine; the
  `qa/` baseline's 09:51 live run arrived via workspace sync from another
  host). So the population below is **reconstructed from committed Oct-9
  recrawl evidence, not a fresh live query**:
  `complete/recrawl_queue.json` (1,642 thin ids, built from the live
  all-`DEFAULT_TEMPLATE` query) minus the 80 funders whose
  `review/recrawl_*` packs show ≥1 extracted opportunity (commit `1001782`)
  = **1,562 funders**. Charity numbers come from the pack headers
  (DB-enriched Oct-9; 1,437 `SC…`, 125 `NIC…`, zero missing).
* **Website seeds** come from `[website]` page entries in those packs only
  (25 true own-sites after excluding CCNI/OSCR profile URLs stored in
  `funders.website` — see method notes). Each was **re-probed live today**
  with the same one-shot httpx homepage GET the live path uses. Funders with
  no website page may have an empty DB website *or* a fetch that failed on
  Oct-9 — the live re-run resolves this.
* **Gaps vs the live path**: funder descriptions live only in the DB, so
  grant-signal scores are all 0 and `manual-review` is empty here; `opp_count`
  is null (placeholder counts not re-verified).
* **Authoritative refresh** (one command, staging-only, read-only, zero AI
  spend — overwrites both files):
  `python complete/triage_thin.py --out complete/triage_thin_sites.json --report complete/triage_summary.md`
* **Upstream flag (not fixed — no-edits rule)**: `complete/crawl_websites.py`
  `REGISTER_PROFILE_HOSTS` misses `charitycommissionni.org.uk` (no hyphen), so
  CCNI profile URLs stored as `funders.website` were crawled as the "website"
  leg on Oct-9. `complete/triage_thin.py` uses its own stricter
  `is_probable_own_website` and is unaffected.

Source: staging `grantseeker_devolved` (SELECT-only, read-only session) at 2026-10-10T09:17:37.399964+00:00.
Population: 1562 funders whose opportunities are ALL `DEFAULT_TEMPLATE`; homepages probed once each over plain httpx (10s timeout, no retries, no browser, no LLM).

## Counts per bucket

| Bucket | Funders | % |
|--------|---------|---|
| crawlable-website | 17 | 1.1% |
| register-only | 1545 | 98.9% |
| dead-end | 0 | 0.0% |
| manual-review | 0 | 0.0% |

## Recommended handling

### crawlable-website (17)

Re-crawl with the dual-source pipeline (website leg will now carry the page); register leg optional. Automatable.

### register-only (1545)

Single-register fetch + DeepSeek pass (cheap, bounded); accept that many will yield trustee-discretion rows with no public programme. Automatable with human spot-checks.

### dead-end (0)

Do not spend crawl/LLM budget. Keep the DEFAULT_TEMPLATE row as an explicit thin marker, or park for a future bulk-register refresh.

### manual-review (0)

Human pass first (~50 highest grant-signal descriptions): confirm whether a real fund exists, then route to the matching lane above.

## Method notes (reproducible)

* Website liveness = one httpx GET of the stored website homepage (10s, follow redirects); live means HTTP 200 + >= 500 chars of tag-stripped text with none of the parked-domain markers (domain for sale, buy this domain, this domain is parked, parked domain, …). Probed 25 own-site homepages.
* Own-site test is stricter than crawl_websites.is_own_website: OSCR, CCNI (charitycommissionni.org.uk) and commission URLs count as register evidence, since Oct-9 recrawl packs show CCNI profile URLs stored in funders.website.
* Register URL built purely from `charity_number`: `SC…` → OSCR profile, `NIC…` → CCNI profile (`complete/crawl_dual_source.register_url`).
* Manual-review = top 50 by grant-signal score on the funder description: 3 pts per GBP-amount mention (`£12,000` / `2 million` style) + 1 pt per distinct fund/grant lexicon word (grant, fund, award, totalling, totaling, distributed, donated, gave, annual giving, trustees meet, application, appeal). Ties break by lowest funder id; score 0 never qualifies, so the bucket can hold fewer than 50.
* Re-run: `python complete/triage_thin.py --out complete/triage_thin_sites.json --report complete/triage_summary.md` (staging only, read-only, zero AI spend).
