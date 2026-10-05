"""Tests for complete/audit_pack.py pure helpers (no DB)."""

from complete.audit_pack import audit_card, build_index, draw_sample


def _mk(i, gm):
    return {"id": i, "name": f"F{i}", "is_grantmaker": gm, "confidence": 0.9,
            "reason": "r", "charity_number": "SC1", "website": "w", "description": "d"}


def test_draw_sample_split_and_seed_stable():
    results = [_mk(i, i % 2 == 0) for i in range(20)]
    a1, r1 = draw_sample(results, 5, seed=7)
    a2, r2 = draw_sample(results, 5, seed=7)
    assert len(a1) == len(r1) == 5
    assert [x["id"] for x in a1] == [x["id"] for x in a2]
    assert all(x["is_grantmaker"] for x in a1) and not any(x["is_grantmaker"] for x in r1)


def test_draw_sample_caps_at_pool_size():
    a, r = draw_sample([_mk(1, True)], 30)
    assert len(a) == 1 and r == []


def test_card_and_index_contents():
    f = _mk(5, True)
    f["oscr_purposes"] = "advancement of education"
    card = audit_card(f)
    assert "GRANTMAKER" in card and "Human verdict" in card
    assert "advancement of education" in card and "Evidence the model saw" in card
    idx = build_index([f], [_mk(6, False)])
    assert "Model accepts" in idx and "Model rejects" in idx and "F5" in idx
