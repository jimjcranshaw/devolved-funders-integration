"""Devolved audit helpers (Issue #1) — pure functions, no DB needed.

Cross-links: oscr-integration/insert_oscr/ccni_funders.py (source_register
values), corporate-partnerships/config.py (DEVOLVED_DB_CONFIG).
"""

from __future__ import annotations


VALID_REGISTERS = ("scotland", "northern_ireland", "england_wales")


def dedupe_key(charity_number: str, name: str) -> tuple[str, str]:
    """Return (register, normalized_number) for cross-register dedupe.

    Name-only ON CONFLICT collapses UK-wide grantmakers registered in all
    three jurisdictions, so the merge key must be charity-number-scoped:
    SC###### -> scotland, NIC###### -> northern_ireland, else england_wales.
    """
    num = (charity_number or "").strip().upper().replace(" ", "")
    if num.startswith("SC"):
        return ("scotland", num)
    if num.startswith("NIC"):
        return ("northern_ireland", num)
    return ("england_wales", num or name.strip().lower())


def coverage_stats(rows: list[dict]) -> dict:
    """Aggregate per-register funder/opportunity coverage.

    Each row: {"source_register": str, "has_opportunity": bool}.
    Unknown registers are counted under their own key, never dropped.
    """
    stats: dict[str, dict[str, float]] = {}
    for row in rows:
        reg = row.get("source_register") or "unknown"
        bucket = stats.setdefault(reg, {"funders": 0, "with_opps": 0})
        bucket["funders"] += 1
        if row.get("has_opportunity"):
            bucket["with_opps"] += 1
    for bucket in stats.values():
        funders = bucket["funders"]
        bucket["without_opps"] = funders - bucket["with_opps"]
        bucket["pct_with_opps"] = round(bucket["with_opps"] / funders * 100, 1) if funders else 0.0
    return stats


def find_stale_registers(last_run_days_ago: dict[str, int], cutoff_days: int = 90) -> list[str]:
    """Return registers whose last successful run is older than cutoff.

    Input maps register -> days since last run. Missing registers are stale.
    """
    stale = [
        reg
        for reg in (*VALID_REGISTERS, *last_run_days_ago)
        if last_run_days_ago.get(reg, cutoff_days + 1) > cutoff_days
    ]
    return sorted(set(stale))
