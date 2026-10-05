"""Human audit pack (Issue #5 validation): 30 accepts + 30 rejects, seeded sample.

Reads a classify sweep file, draws a reproducible random sample
(seed 42), enriches from DB, writes one markdown file per funder plus an
index checklist the human fills in.

Usage:
  python complete/audit_pack.py --from complete/full_live.json --out-dir review/audit --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AUDIT_N_EACH = 30


def draw_sample(results: list[dict], n_each: int = AUDIT_N_EACH,
                seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Split into model accepts/rejects and draw n_each from each. Pure."""
    rng = random.Random(seed)
    accepts = [r for r in results if r.get("is_grantmaker")]
    rejects = [r for r in results if not r.get("is_grantmaker")]
    return (rng.sample(accepts, min(n_each, len(accepts))),
            rng.sample(rejects, min(n_each, len(rejects))))


EVIDENCE_FIELDS = (("oscr_purposes", "OSCR purposes"),
                   ("objects", "Objects"), ("activities", "Activities"),
                   ("constitutional_form", "Constitutional form"),
                   ("ukcat_codes", "UKCAT codes"),
                   ("initial_classification", "Import classification"))


def audit_card(funder: dict) -> str:
    """One human-review markdown card, showing the model's evidence. Pure."""
    verdict = "GRANTMAKER" if funder.get("is_grantmaker") else "NOT grantmaker"
    lines = [f"# {funder.get('name', '')} (id {funder.get('id', '')})", "",
             f"Charity number: {funder.get('charity_number', '')}",
             f"Website: {funder.get('website', '')}",
             f"Description: {(funder.get('description', '') or '')[:800]}", "",
             "## Evidence the model saw"]
    for key, label in EVIDENCE_FIELDS:
        val = funder.get(key)
        if val:
            lines.append(f"- **{label}**: {str(val)[:800]}")
    lines += ["",
              f"## Model says: {verdict} (confidence {funder.get('confidence', '?')})",
              f"Reason: {funder.get('reason', '')}", "",
              "## Human verdict (fill in): GRANTMAKER / NOT — notes:", ""]
    return "\n".join(lines)


def build_index(accepts: list[dict], rejects: list[dict]) -> str:
    """Checklist index over both samples. Pure."""
    lines = ["# Audit pack index", "",
             "Mark each: agree / overturn.", "",
             "## Model accepts (says grantmaker)"]
    for f in accepts:
        lines.append(f"- [ ] {f['id']} | {f['name']} | conf {f.get('confidence', '?')} | {f.get('reason', '')}")
    lines += ["", "## Model rejects (says NOT grantmaker)"]
    for f in rejects:
        lines.append(f"- [ ] {f['id']} | {f['name']} | conf {f.get('confidence', '?')} | {f.get('reason', '')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_file", required=True)
    ap.add_argument("--out-dir", default="review/audit")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-each", type=int, default=AUDIT_N_EACH)
    args = ap.parse_args()

    from complete.extract_programmes import enrich_from_db

    data = json.loads(Path(args.from_file).read_text())
    accepts, rejects = draw_sample(data.get("results", []), args.n_each, args.seed)
    try:
        enrich_from_db(accepts)
        enrich_from_db(rejects)
    except Exception as e:
        print(f"Warning: DB enrich failed ({e}).")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in accepts + rejects:
        (out / f"{f['id']}.md").write_text(audit_card(f))
    (out / "INDEX.md").write_text(build_index(accepts, rejects))
    print(f"Wrote {len(accepts)} accepts + {len(rejects)} rejects -> {out}/ (+ INDEX.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
