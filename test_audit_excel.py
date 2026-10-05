"""Tests for complete/audit_excel.py workbook builder (no DB)."""

from complete.audit_excel import COLUMNS, _row_values, build_workbook


def _mk(i, gm):
    return {"id": i, "name": f"F{i}", "charity_number": "SC1", "website": "https://w",
            "description": "d", "oscr_purposes": "education",
            "is_grantmaker": gm, "confidence": 0.9, "reason": "r"}


def test_sheets_headers_and_rows():
    wb = build_workbook([_mk(1, True)], [_mk(2, False)])
    assert wb.sheetnames == ["Accepts", "Rejects"]
    ws = wb["Accepts"]
    assert [ws.cell(row=2, column=c).value for c in range(1, len(COLUMNS) + 1)] == COLUMNS
    assert ws.cell(row=3, column=1).value == 1
    assert "OSCR purposes" in COLUMNS
    assert ws.freeze_panes == "A3"
    assert wb["Rejects"].cell(row=3, column=1).value == 2


def test_row_values_carry_evidence():
    vals = _row_values(_mk(1, True))
    assert vals[COLUMNS.index("OSCR purposes")] == "education"
    assert vals[COLUMNS.index("Model verdict")] == "GRANTMAKER"
    assert vals[COLUMNS.index("Human")] == ""
