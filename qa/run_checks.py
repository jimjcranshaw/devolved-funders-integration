"""Devolved QA baseline (Issue #3) — deterministic, read-only, zero API spend.

Adapts the B1/B3/B5/B6/B7/B8 rubric from the qa-azure-funders skill to what
the devolved staging schema actually supports. No `pages` table exists on
`grantseeker_devolved`, so B1/B5/B8 are reported BLOCKED (with unblock notes)
instead of scored. Everything else is pure SQL + string rules — no LLM.

Usage (from repo root so .env loads):
  python qa/run_checks.py --out qa/results_20261010.json --report qa/report_20261010.md

Safety:
  * SELECT-only queries; the session sets `SET SESSION CHARACTERISTICS AS
    TRANSACTION READ ONLY` via `conn.readonly = True`.
  * Refuses to run unless the target is the devolved staging database
    (`grantseeker_devolved`, never `grantseeker-pg-server`).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Explicit .env path: bare load_dotenv() relies on find_dotenv(), which misses
# the repo-root .env under some Windows setups. Explicit path works everywhere.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)

# ---------------------------------------------------------------------------
# Pure scoring helpers (no DB — covered by qa/test_run_checks.py)
# ---------------------------------------------------------------------------

GENERIC_TITLES = frozenset({
    # From the qa-azure-funders rubric (lowercased).
    "main grants", "grant programme", "funding", "grants", "programme",
    "grant program", "funding programme", "funding program",
    # Observed DEFAULT_TEMPLATE / legacy boilerplate on devolved staging.
    "untitled",
    "general grant programme", "general grants programme",
    "general grantmaking", "general grantmaking programme",
    "general grantmaking - assessment needed",
    "general grantmaking - grantmaking charity",
    "general grant funding", "general funding programme",
    "general funding support", "general grant distribution",
    "general grant support", "general discretionary grants",
    "general benevolent fund", "general benevolent fund grants",
    "general charitable grants", "general charitable purposes fund",
    "general community grants programme", "general community support grants",
    "small grants", "small grants scheme",
    "community grant", "community grants", "community fund",
})

# Any title starting with "general " is template-derived boilerplate on this
# dataset (every observed instance traces to DEFAULT_TEMPLATE/AI_ANALYSIS
# rows). Kept as a separate rule so the report can show exactly which titles
# it caught.
GENERIC_PREFIX = "general "

VAGUE_BENEFICIARY_PATTERNS = (
    "no specific group",
    "general public",
    "the public",
    "benefit of the community",
    "other charities or voluntary bodies",
    "other defined groups",
)

#: Content columns counted for the B7 populated-field tally. Excludes ids,
#: provenance metadata, timestamps and the embedding vector.
CONTENT_FIELDS = (
    "opportunity_title", "description",
    "eligibility_inclusion", "eligibility_exclusion",
    "funding_focus", "funding_amounts", "deadlines",
    "application_process", "application_requirements",
    "evaluation_criteria", "contact_info",
    "application_form_url", "application_form_type",
    "application_questions", "guidance_url", "guidance_text",
    "objectives_goals", "important_urls", "ukcat_codes",
    "min_amount", "max_amount", "deadline",
    "eligibility_criteria", "beneficiary_groups",
    "excludes_individuals", "excludes_for_profit",
    "excludes_religious", "excludes_political",
)

#: New E&W-parity columns whose fill rates are reported individually.
NEW_COLUMNS = (
    "min_amount", "max_amount", "deadline",
    "eligibility_criteria", "beneficiary_groups",
    "excludes_individuals", "excludes_for_profit",
    "excludes_religious", "excludes_political",
)

LEGACY_AMOUNT_COLS = ("funding_amounts",)
LEGACY_DEADLINE_COLS = ("deadlines",)

PLACEHOLDER_SOURCE = "DEFAULT_TEMPLATE"
B7_MAX_FIELDS = 5


def normalize_title(title: object) -> str:
    return (title or "").strip() if isinstance(title, str) else ""


def is_generic_title(title: object, funder_name: object = "") -> bool:
    """True for placeholder titles: null/empty, rubric generics, `general *`
    boilerplate, or the funder name repeated as the title."""
    t = normalize_title(title)
    if not t:
        return True
    low = t.lower()
    if low in GENERIC_TITLES:
        return True
    if low.startswith(GENERIC_PREFIX):
        return True
    funder = normalize_title(funder_name)
    if funder and low == funder.lower():
        return True
    return False


def is_populated(value: object) -> bool:
    """A field counts as populated when it carries data: non-null, with
    strings/collections required to be non-empty after stripping. An explicit
    boolean FALSE (e.g. `excludes_individuals = false`) counts as populated —
    it is an asserted value, not a gap."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict, bytes)):
        return len(value) > 0
    return True


def count_populated(row: dict, fields: tuple[str, ...] = CONTENT_FIELDS) -> int:
    return sum(1 for f in fields if is_populated(row.get(f)))


def count_informative(row: dict, fields: tuple[str, ...] = CONTENT_FIELDS) -> int:
    """Populated-field tally for B7 with schema defaults discounted: every
    excludes_* column defaults to FALSE, so a FALSE carries no extracted
    information and must not shield a placeholder row from the <=5 rule.
    Only TRUE booleans count."""
    n = 0
    for f in fields:
        v = row.get(f)
        if v is False:
            continue
        if is_populated(v):
            n += 1
    return n


def is_placeholder(row: dict) -> bool:
    """Rubric B7 adapted: <=5 informative fields AND a generic title."""
    return (
        count_informative(row) <= B7_MAX_FIELDS
        and is_generic_title(row.get("opportunity_title"), row.get("funder_name"))
    )


def is_substantive_beneficiary(value: object) -> bool:
    """Rubric B6 adapted: beneficiary_groups must be present and free of
    vague catch-all phrasing (register-purpose boilerplate such as
    'No specific group, or for the benefit of the community')."""
    if not is_populated(value):
        return False
    if isinstance(value, (list, tuple, set)):
        text = "; ".join(str(v) for v in value).lower()
    else:
        text = str(value).lower()
    return not any(p in text for p in VAGUE_BENEFICIARY_PATTERNS)


def provenance_group(source: object) -> str:
    """Bucket opportunity_source into new dual-/single-* rows vs legacy."""
    s = (source or "") if isinstance(source, str) else ""
    if s.startswith("dual-"):
        return "new-dual"
    if s.startswith("single-"):
        return "new-single"
    return "legacy"


def pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


# ---------------------------------------------------------------------------
# DB checks (SELECT-only)
# ---------------------------------------------------------------------------

OPP_SELECT_COLS = (
    "o.id, o.funder_id, f.name AS funder_name, "
    "f.source_register, o.opportunity_source, o.is_default_assessment, "
    + ", ".join(f"o.{c}" for c in CONTENT_FIELDS)
)


def run_checks() -> dict:
    import os

    import psycopg2
    import psycopg2.extras

    host = os.getenv("DB_HOST", "psql-grantsai-db.postgres.database.azure.com")
    database = os.getenv("DEVOLVED_DB_NAME", "grantseeker_devolved")
    if "grantseeker-pg-server" in (host or ""):
        raise SystemExit("REFUSAL: DB_HOST points at prod (grantseeker-pg-server). "
                         "This suite runs on staging grantseeker_devolved only.")
    if database != "grantseeker_devolved":
        raise SystemExit(f"REFUSAL: DEVOLVED_DB_NAME={database!r}; "
                         "expected 'grantseeker_devolved'.")
    if not os.getenv("DB_PASSWORD"):
        raise SystemExit("Missing DB_PASSWORD in .env.")

    conn = psycopg2.connect(
        host=host, user=os.getenv("DB_USER", "grantsadmin"),
        password=os.getenv("DB_PASSWORD", ""),
        database=database, port=int(os.getenv("DB_PORT", "5432")),
        sslmode="require",
    )
    # Belt-and-braces: read-only session on top of SELECT-only statements.
    conn.readonly = True
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT to_regclass('public.pages') AS pages")
            pages_present = cur.fetchone()["pages"] is not None

            cur.execute("SELECT source_register, COUNT(*) AS n FROM funders "
                        "GROUP BY 1 ORDER BY 1")
            funders_by_register = [
                {"source_register": r["source_register"], "funders": r["n"]}
                for r in cur.fetchall()
            ]

            cur.execute(
                "SELECT f.source_register AS reg, "
                "COUNT(DISTINCT f.id) AS funders, "
                "COUNT(DISTINCT CASE WHEN o.id IS NOT NULL THEN f.id END) AS with_opp, "
                "COUNT(DISTINCT CASE WHEN o.opportunity_source <> 'DEFAULT_TEMPLATE' "
                "THEN f.id END) AS with_non_placeholder "
                "FROM funders f LEFT JOIN funding_opportunities o "
                "ON o.funder_id = f.id GROUP BY 1 ORDER BY 1"
            )
            funder_coverage = [dict(r) for r in cur.fetchall()]

            cur.execute(
                f"SELECT {OPP_SELECT_COLS} FROM funding_opportunities o "
                "JOIN funders f ON f.id = o.funder_id ORDER BY o.id ASC"
            )
            opps = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    total = len(opps)
    generic_ids, placeholder_ids, substantive_b6 = [], [], 0
    title_freq: dict[str, int] = {}
    flagged_title_freq: dict[str, int] = {}
    source_freq: dict[str, int] = {}
    group_freq: dict[str, int] = {"new-dual": 0, "new-single": 0, "legacy": 0}
    fill_total: dict[str, int] = {c: 0 for c in NEW_COLUMNS + LEGACY_AMOUNT_COLS + LEGACY_DEADLINE_COLS}
    fill_by_group: dict[str, dict[str, int]] = {}
    fill_by_register: dict[str, dict[str, int]] = {}
    group_totals: dict[str, int] = {}
    register_totals: dict[str, int] = {}
    bene_freq: dict[str, int] = {}

    for o in opps:
        generic = is_generic_title(o.get("opportunity_title"), o.get("funder_name"))
        if generic:
            generic_ids.append(o["id"])
        if is_placeholder(o):
            placeholder_ids.append(o["id"])
        if is_substantive_beneficiary(o.get("beneficiary_groups")):
            substantive_b6 += 1

        title = normalize_title(o.get("opportunity_title")) or "<null/empty>"
        title_freq[title] = title_freq.get(title, 0) + 1
        if generic:
            flagged_title_freq[title] = flagged_title_freq.get(title, 0) + 1

        src = o.get("opportunity_source") or "<null>"
        source_freq[src] = source_freq.get(src, 0) + 1
        grp = provenance_group(o.get("opportunity_source"))
        group_freq[grp] += 1
        group_totals[grp] = group_totals.get(grp, 0) + 1
        reg = o.get("source_register") or "unknown"
        register_totals[reg] = register_totals.get(reg, 0) + 1

        for col in fill_total:
            if is_populated(o.get(col)):
                fill_total[col] += 1
                fill_by_group.setdefault(grp, {}).setdefault(col, 0)
                fill_by_group[grp][col] += 1
                fill_by_register.setdefault(reg, {}).setdefault(col, 0)
                fill_by_register[reg][col] += 1

        bene = o.get("beneficiary_groups")
        bene_key = "; ".join(bene) if isinstance(bene, (list, tuple)) else (bene if bene else "<null>")
        bene_freq[bene_key] = bene_freq.get(bene_key, 0) + 1

    new_tagged = group_freq["new-dual"] + group_freq["new-single"]
    top = lambda freq, n=25: [
        {"value": k, "count": v} for k, v in sorted(freq.items(), key=lambda kv: -kv[1])[:n]
    ]

    return {
        "meta": {
            "at": datetime.now(timezone.utc).isoformat(),
            "db": database,
            "host": host,
            "total_funders": sum(r["funders"] for r in funders_by_register),
            "total_opportunities": total,
            "funders_by_register": funders_by_register,
            "pages_table_present": pages_present,
            "read_only_session": True,
        },
        "scores": {
            "B3_title_specificity": {
                "threshold_pct": 90, "direction": ">=",
                "specific": total - len(generic_ids), "total": total,
                "pct": pct(total - len(generic_ids), total),
                "pass": pct(total - len(generic_ids), total) >= 90,
                "generic_ids": generic_ids,
            },
            "B6_beneficiary_specificity": {
                "threshold_pct": 80, "direction": ">=",
                "substantive": substantive_b6, "total": total,
                "pct": pct(substantive_b6, total),
                "pass": pct(substantive_b6, total) >= 80,
            },
            "B7_default_only": {
                "threshold_pct": 20, "direction": "<=",
                "placeholder": len(placeholder_ids), "total": total,
                "pct": pct(len(placeholder_ids), total),
                "pass": pct(len(placeholder_ids), total) <= 20,
                "placeholder_ids": placeholder_ids,
            },
        },
        "blocked": {
            "B1_faithfulness": {
                "threshold": ">=95%",
                "status": "BLOCKED",
                "reason": "No pages.content table on devolved staging "
                          f"(to_regclass -> {'present' if pages_present else 'absent'}); "
                          "field-to-source traceability cannot be checked.",
                "unblock": "Persist scraped source text per funder (pages table "
                           "or review-pack corpus link), then re-run B1 as "
                           "fields_with_source_support / total_populated_fields.",
            },
            "B5_exclusion_precision": {
                "threshold": ">=95%",
                "status": "BLOCKED",
                "reason": "Every excludes_* TRUE needs an explicit exclusion "
                          "statement in source text; no source text is stored.",
                "unblock": "Same source-text store as B1; then verify each TRUE "
                           "excludes_* against phrases like 'does not fund' / "
                           "'not eligible' near a matching subject.",
            },
            "B8_description_quality": {
                "threshold": ">=90%",
                "status": "BLOCKED",
                "reason": "Programme-specificity vs boilerplate cannot be judged "
                          "without the source page text the description was drawn from.",
                "unblock": "Same source-text store as B1; then score descriptions "
                           "for specific programme detail (>=30 words + no pure "
                           "boilerplate patterns).",
            },
        },
        "fill_rates": {
            "overall": {c: {"filled": fill_total[c], "total": total,
                            "pct": pct(fill_total[c], total)} for c in fill_total},
            "by_provenance": {
                grp: {c: {"filled": fill_by_group.get(grp, {}).get(c, 0),
                          "total": group_totals.get(grp, 0),
                          "pct": pct(fill_by_group.get(grp, {}).get(c, 0),
                                     group_totals.get(grp, 0))} for c in NEW_COLUMNS}
                for grp in ("new-dual", "new-single", "legacy")
            },
            "by_register": {
                reg: {c: {"filled": fill_by_register.get(reg, {}).get(c, 0),
                          "total": register_totals.get(reg, 0),
                          "pct": pct(fill_by_register.get(reg, {}).get(c, 0),
                                     register_totals.get(reg, 0))} for c in NEW_COLUMNS}
                for reg in sorted(register_totals)
            },
        },
        "source_label_coverage": {
            "by_source": source_freq,
            "by_group": group_freq,
            "tagged_new_pct": pct(new_tagged, total),
            "tagged_new": new_tagged, "total": total,
        },
        "funder_coverage": [
            {**r,
             "pct_with_opp": pct(r["with_opp"], r["funders"]),
             "pct_with_non_placeholder": pct(r["with_non_placeholder"], r["funders"])}
            for r in funder_coverage
        ],
        "evidence": {
            "top_titles": top(title_freq),
            "flagged_generic_titles": top(flagged_title_freq),
            "top_beneficiary_values": top(bene_freq, 15),
        },
    }


def render_markdown(data: dict) -> str:
    """Render the markdown QA report from a results dict. Pure."""
    m = data["meta"]
    s = data["scores"]
    lines = [
        f"# QA Baseline — devolved staging ({m['db']}) — {m['at'][:10]}",
        "",
        f"Scope: {m['total_funders']:,} funders, "
        f"{m['total_opportunities']:,} opportunities, full-table (not a sample). "
        "Deterministic DB checks only — no LLM, zero API spend. "
        "Session ran with `TRANSACTION READ ONLY`; every query SELECT-only.",
        "",
        "## Verdict",
        "",
    ]
    scored_pass = all(v["pass"] for v in s.values())
    lines.append(
        "**FAIL** — at least one scored benchmark misses its threshold "
        "(B1/B5/B8 blocked, see below)." if not scored_pass else "**PASS**."
    )
    lines += ["", "## Scored benchmarks", "",
              "| Benchmark | Score | Threshold | Status |",
              "|-----------|-------|-----------|--------|"]
    for key, label in (("B3_title_specificity", "B3 Title specificity"),
                       ("B6_beneficiary_specificity", "B6 Beneficiary specificity"),
                       ("B7_default_only", "B7 Default-only")):
        v = s[key]
        num = v.get("specific", v.get("substantive", v.get("placeholder")))
        lines.append(f"| {label} | {num}/{v['total']} ({v['pct']}%) "
                     f"| {v['direction']}{v['threshold_pct']}% "
                     f"| {'PASS' if v['pass'] else 'FAIL'} |")
    lines += ["", "## Blocked (no pages table on devolved)", ""]
    for key, label in (("B1_faithfulness", "B1 Faithfulness"),
                       ("B5_exclusion_precision", "B5 Exclusion precision"),
                       ("B8_description_quality", "B8 Description quality")):
        b = data["blocked"][key]
        lines += [f"### {label} — {b['status']} (threshold {b['threshold']})",
                  f"Reason: {b['reason']}", f"Unblock: {b['unblock']}", ""]
    lines += ["## Field fill rates (overall)", "",
              "| Column | Filled / total | % |",
              "|--------|----------------|---|"]
    for col, v in data["fill_rates"]["overall"].items():
        lines.append(f"| {col} | {v['filled']}/{v['total']} | {v['pct']}% |")
    lines += ["", "## Fill rates of new E&W columns by provenance", ""]
    header = "| Column | " + " | ".join(("new-dual", "new-single", "legacy")) + " |"
    lines += [header, "|" + "---|" * 4]
    prov_cols = [c for c in NEW_COLUMNS if c in data["fill_rates"]["overall"]]
    for col in prov_cols:
        cells = [f"{data['fill_rates']['by_provenance'][g][col]['pct']}%"
                 for g in ("new-dual", "new-single", "legacy")]
        lines.append(f"| {col} | " + " | ".join(cells) + " |")
    lines += ["", "## Fill rates of new E&W columns by register", ""]
    regs = sorted(data["fill_rates"]["by_register"])
    lines += ["| Column | " + " | ".join(regs) + " |", "|" + "---|" * (len(regs) + 1)]
    for col in prov_cols:
        cells = [f"{data['fill_rates']['by_register'][r][col]['pct']}% " for r in regs]
        lines.append(f"| {col} | " + " | ".join(c.strip() for c in cells) + " |")
    sl = data["source_label_coverage"]
    lines += ["", "## Source-label coverage", "",
              f"Tagged dual-*/single-*: {sl['tagged_new']}/{sl['total']} "
              f"({sl['tagged_new_pct']}%). Untagged legacy rows carry the "
              "pre-dual-source provenance.",
              "",
              "| opportunity_source | Rows |",
              "|--------------------|------|"]
    for src, n in sorted(sl["by_source"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {src} | {n} |")
    lines += ["", "## Funder coverage (non-placeholder opp per register)", "",
              "| Register | Funders | With >=1 opp | With >=1 non-placeholder |",
              "|----------|---------|--------------|----------------------------|"]
    for r in data["funder_coverage"]:
        lines.append(f"| {r['reg']} | {r['funders']} | {r['with_opp']} "
                     f"({r['pct_with_opp']}%) | {r['with_non_placeholder']} "
                     f"({r['pct_with_non_placeholder']}%) |")
    lines += ["", "## Evidence: top titles (flagged *)", ""]
    flagged = {e["value"] for e in data["evidence"]["flagged_generic_titles"]}
    for e in data["evidence"]["top_titles"]:
        mark = " *" if e["value"] in flagged else ""
        lines.append(f"- {e['count']:>5}  {e['value']}{mark}")
    lines += ["", "## Evidence: top beneficiary_groups values", ""]
    for e in data["evidence"]["top_beneficiary_values"]:
        lines.append(f"- {e['count']:>5}  {e['value']}")
    lines += ["",
              f"B3 generic ids: {len(s['B3_title_specificity']['generic_ids'])} "
              f"(full list in JSON). "
              f"B7 placeholder ids: {len(s['B7_default_only']['placeholder_ids'])} "
              "(full list in JSON).",
              ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="JSON results path")
    ap.add_argument("--report", required=True, help="Markdown report path")
    args = ap.parse_args()
    data = run_checks()
    out = Path(args.out)
    out.write_text(json.dumps(data, indent=2, default=str))
    print(f"Wrote {out}: {data['meta']['total_opportunities']} opps scored")
    rep = Path(args.report)
    rep.write_text(render_markdown(data))
    print(f"Wrote {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
