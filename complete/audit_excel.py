"""Excel audit pack (Issue #5 validation): formatted workbook, same seed sample.

Two sheets (Accepts, Rejects): frozen header, autofilter, wrapped text,
confidence as number, verdict colour band, empty Human/Notes columns with an
AGREE-or-OVERTURN dropdown on Human.

Usage:
  python complete/audit_excel.py --from complete/full_live.json --out review/audit_pack.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties

from complete.audit_pack import AUDIT_N_EACH, draw_sample

COLUMNS = ["ID", "Name", "Charity number", "Website", "Description",
           "OSCR purposes", "Objects / Activities", "Form / UKCAT",
           "Model verdict", "Confidence", "Model reason", "Human", "Notes"]
WIDTHS = [8, 34, 14, 26, 50, 36, 36, 20, 14, 11, 34, 12, 26]
WRAP_COLS = {"Description", "OSCR purposes", "Objects / Activities", "Model reason", "Notes"}


def _row_values(f: dict) -> list:
    desc = (f.get("description") or "")[:2000]
    purposes = str(f.get("oscr_purposes") or f.get("purposes") or "")[:1200]
    objects = " / ".join(s for s in [str(f.get("objects") or "")[:800],
                                     str(f.get("activities") or "")[:800]] if s)
    form = " / ".join(s for s in [str(f.get("constitutional_form") or ""),
                                  str(f.get("ukcat_codes") or "")] if s)
    verdict = "GRANTMAKER" if f.get("is_grantmaker") else "NOT grantmaker"
    return [f.get("id"), f.get("name"), f.get("charity_number"), f.get("website"),
            desc, purposes, objects, form, verdict,
            f.get("confidence"), f.get("reason"), "", ""]

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
ACCEPT_FILL = PatternFill("solid", fgColor="E2EFDA")
REJECT_FILL = PatternFill("solid", fgColor="FCE4EC")
THIN = Side(style="thin", color="B0B0B0")
THIN_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def style_sheet(ws, title: str, rows: list[dict], band: PatternFill) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=13, color="1F4E78")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS))
    for ci, col in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=2, column=ci, value=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(ci)].width = WIDTHS[ci - 1]
    dv = DataValidation(type="list", formula1='"AGREE,OVERTURN"', allow_blank=True)
    dv.prompt = "AGREE or OVERTURN?"
    ws.add_data_validation(dv)
    for ri, f in enumerate(rows, start=3):
        values = _row_values(f)
        for ci, val in enumerate(values, start=1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.border = THIN_BORDER
            cell.fill = band
            if COLUMNS[ci - 1] in WRAP_COLS:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            else:
                cell.alignment = Alignment(vertical="top")
        ws.row_dimensions[ri].height = 45
    last = 2 + len(rows)
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(COLUMNS))}{last}"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    # confidence colour scale on its column
    conf_col = get_column_letter(COLUMNS.index("Confidence") + 1)
    ws.conditional_formatting.add(
        f"{conf_col}3:{conf_col}{last}",
        CellIsRule(operator="greaterThan", formula=["0.85"],
                   fill=PatternFill("solid", fgColor="C6EFCE")))
    ws.sheet_view.showGridLines = False


def build_workbook(accepts: list[dict], rejects: list[dict]) -> Workbook:
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Accepts"
    style_sheet(ws1, "Model accepts — says GRANTMAKER (30)", accepts, ACCEPT_FILL)
    ws2 = wb.create_sheet("Rejects")
    style_sheet(ws2, "Model rejects — says NOT a grantmaker (30)", rejects, REJECT_FILL)
    return wb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_file", required=True)
    ap.add_argument("--out", default="review/audit_pack.xlsx")
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
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(accepts, rejects).save(out)
    print(f"Wrote {out}: {len(accepts)} accepts + {len(rejects)} rejects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
