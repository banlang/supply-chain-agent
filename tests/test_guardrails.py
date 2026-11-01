"""
Tests for src/guardrails.py

inventory_output_guardrail  — rejects hallucinated/missing inventory data
risk_output_guardrail       — rejects invented risk scale labels
reorder_output_guardrail    — validates supplier, action, qty, zero-stock rule
portfolio_output_guardrail  — requires cross-SKU references (≥ 2 distinct SKUs) and
                              fact-checks counts, SKUs and suppliers against Agent 3

Guardrails load the real CSV at import time (_ZERO_STOCK_SKUS etc.).
The actual dataset must be present at data/supply_chain_data.csv.
"""
import json
import pytest
from unittest.mock import MagicMock

from src.guardrails import (
    inventory_output_guardrail,
    risk_output_guardrail,
    reorder_output_guardrail,
    portfolio_output_guardrail,
    parse_decisions,
    _ZERO_STOCK_SKUS,
    _EXPECTED_SKU_COUNT,
)


def _mock_output(raw: str):
    """Return a minimal TaskOutput-like object with .raw and .pydantic=None."""
    m = MagicMock()
    m.raw = raw
    m.pydantic = None
    return m


# ---------------------------------------------------------------------------
# inventory_output_guardrail
# ---------------------------------------------------------------------------

class TestInventoryOutputGuardrail:
    def test_passes_valid_output(self):
        raw = f"Total SKUs analyzed: {_EXPECTED_SKU_COUNT}\nSKU68 at risk\nSKU2 needs review"
        ok, _ = inventory_output_guardrail(_mock_output(raw))
        assert ok is True

    def test_fails_no_sku_codes(self):
        raw = f"Total SKUs analyzed: {_EXPECTED_SKU_COUNT}\nNo specific products mentioned."
        ok, msg = inventory_output_guardrail(_mock_output(raw))
        assert ok is False
        assert "SKU" in msg

    def test_fails_wrong_count(self):
        raw = "Total SKUs analyzed: 50\nSKU1 SKU2 at risk"
        ok, msg = inventory_output_guardrail(_mock_output(raw))
        assert ok is False
        assert "50" in msg

    def test_fails_missing_count_line(self):
        raw = "SKU1 and SKU2 are at risk of stockout"
        ok, msg = inventory_output_guardrail(_mock_output(raw))
        assert ok is False

    def test_fails_empty_output(self):
        ok, _ = inventory_output_guardrail(_mock_output(""))
        assert ok is False


# ---------------------------------------------------------------------------
# risk_output_guardrail
# ---------------------------------------------------------------------------

class TestRiskOutputGuardrail:
    def test_passes_high(self):
        ok, _ = risk_output_guardrail(_mock_output("SKU68: High risk score=3"))
        assert ok is True

    def test_passes_critical(self):
        ok, _ = risk_output_guardrail(_mock_output("SKU2: Critical, score=6"))
        assert ok is True

    def test_passes_medium(self):
        ok, _ = risk_output_guardrail(_mock_output("SKU16: Medium risk"))
        assert ok is True

    def test_fails_very_high(self):
        # "Very High Risk" contains "High Risk" which is also in the blocklist —
        # either match is a valid rejection; just confirm the guardrail fires.
        ok, _ = risk_output_guardrail(_mock_output("SKU68: Very High Risk"))
        assert ok is False

    def test_fails_severe(self):
        ok, _ = risk_output_guardrail(_mock_output("SKU68: Severe risk detected"))
        assert ok is False

    def test_fails_extreme(self):
        ok, _ = risk_output_guardrail(_mock_output("SKU68: Extreme risk"))
        assert ok is False

    def test_fails_no_valid_level_at_all(self):
        ok, _ = risk_output_guardrail(_mock_output("SKU68 has some kind of concern"))
        assert ok is False


# ---------------------------------------------------------------------------
# reorder_output_guardrail
# ---------------------------------------------------------------------------

def _reorder_raw(sku="SKU2", supplier="Supplier 3", action="REORDER", qty=9):
    data = {"decisions": [
        {"sku": sku, "recommended_supplier": supplier,
         "action": action, "order_quantity": qty}
    ]}
    return json.dumps(data)


class TestReorderOutputGuardrail:
    def test_passes_valid(self):
        ok, _ = reorder_output_guardrail(_mock_output(_reorder_raw()))
        assert ok is True

    def test_fails_invalid_supplier(self):
        ok, msg = reorder_output_guardrail(
            _mock_output(_reorder_raw(supplier="Best Supplier"))
        )
        assert ok is False
        assert "Best Supplier" in msg

    def test_fails_invalid_action(self):
        ok, _ = reorder_output_guardrail(
            _mock_output(_reorder_raw(action="CRITICAL_REORDER"))
        )
        assert ok is False

    def test_fails_zero_quantity(self):
        ok, _ = reorder_output_guardrail(_mock_output(_reorder_raw(qty=0)))
        assert ok is False

    def test_fails_negative_quantity(self):
        ok, _ = reorder_output_guardrail(_mock_output(_reorder_raw(qty=-10)))
        assert ok is False

    def test_fails_zero_stock_sku_not_urgent(self):
        """A SKU with stock=0 in the real CSV must receive URGENT_REORDER."""
        if not _ZERO_STOCK_SKUS:
            pytest.skip("No zero-stock SKUs in current dataset")
        zero_sku = next(iter(_ZERO_STOCK_SKUS))
        ok, msg = reorder_output_guardrail(
            _mock_output(_reorder_raw(sku=zero_sku, action="REORDER"))
        )
        assert ok is False
        assert "URGENT_REORDER" in msg

    def test_passes_zero_stock_sku_urgent(self):
        if not _ZERO_STOCK_SKUS:
            pytest.skip("No zero-stock SKUs in current dataset")
        zero_sku = next(iter(_ZERO_STOCK_SKUS))
        ok, _ = reorder_output_guardrail(
            _mock_output(_reorder_raw(sku=zero_sku, action="URGENT_REORDER", qty=100))
        )
        assert ok is True

    def test_passes_all_valid_actions(self):
        for action in ("URGENT_REORDER", "REORDER", "MONITOR"):
            ok, _ = reorder_output_guardrail(_mock_output(_reorder_raw(action=action)))
            assert ok is True

    def test_passes_all_valid_suppliers(self):
        for supplier in ("Supplier 1", "Supplier 2", "Supplier 3", "Supplier 4", "Supplier 5"):
            ok, _ = reorder_output_guardrail(_mock_output(_reorder_raw(supplier=supplier)))
            assert ok is True


# ---------------------------------------------------------------------------
# portfolio_output_guardrail
# ---------------------------------------------------------------------------

class TestPortfolioOutputGuardrail:
    def test_passes_two_skus(self):
        raw = '{"patterns": ["SKU68 and SKU2 both haircare"], "concentration_risks": [], "executive_summary": "Summary."}'
        ok, _ = portfolio_output_guardrail(_mock_output(raw))
        assert ok is True

    def test_passes_multiple_skus(self):
        raw = "SKU2 SKU24 SKU68 SKU16 SKU34 all at risk"
        ok, _ = portfolio_output_guardrail(_mock_output(raw))
        assert ok is True

    def test_fails_single_sku_only(self):
        raw = "SKU68 has zero stock and a failed inspection."
        ok, msg = portfolio_output_guardrail(_mock_output(raw))
        assert ok is False
        assert "2" in msg  # message mentions needing 2 SKUs

    def test_fails_no_sku_references(self):
        raw = "The portfolio has several at-risk products with supplier concentration."
        ok, _ = portfolio_output_guardrail(_mock_output(raw))
        assert ok is False

    def test_fails_empty_output(self):
        ok, _ = portfolio_output_guardrail(_mock_output(""))
        assert ok is False


# ---------------------------------------------------------------------------
# portfolio_output_guardrail — fact check against Agent 3 decisions
# ---------------------------------------------------------------------------

# Mirrors the 5 reorder decisions in the demo run:
# haircare = SKU2, SKU24, SKU68 · skincare = SKU16, SKU34
# Supplier 3 = SKU2, SKU24 · Supplier 4 = SKU68 · Supplier 1 = SKU16, SKU34
# Order quantities: SKU2=9, SKU24=224, SKU68=119, SKU16=186, SKU34=400
# Dataset inspections: SKU2=Pending, SKU24=Pending, SKU68=Fail
_DECISIONS = [
    {"sku": "SKU2",  "product_type": "haircare", "action": "REORDER",        "recommended_supplier": "Supplier 3", "order_quantity": 9},
    {"sku": "SKU24", "product_type": "haircare", "action": "REORDER",        "recommended_supplier": "Supplier 3", "order_quantity": 224},
    {"sku": "SKU68", "product_type": "haircare", "action": "URGENT_REORDER", "recommended_supplier": "Supplier 4", "order_quantity": 119},
    {"sku": "SKU16", "product_type": "skincare", "action": "MONITOR",        "recommended_supplier": "Supplier 1", "order_quantity": 186},
    {"sku": "SKU34", "product_type": "skincare", "action": "MONITOR",        "recommended_supplier": "Supplier 1", "order_quantity": 400},
]

_VALID_SUMMARY = "Supplier 3 covers SKU2 and SKU24; SKU68 is the only URGENT_REORDER."


def _insight(*patterns, risks=(), summary=_VALID_SUMMARY):
    return _mock_output(json.dumps({
        "patterns": list(patterns),
        "concentration_risks": list(risks),
        "executive_summary": summary,
    }))


def _check(*patterns, **kw):
    return portfolio_output_guardrail(_insight(*patterns, **kw), _DECISIONS)


class TestParseDecisions:
    def test_parses_reorder_report_json(self):
        raw = json.dumps({"decisions": _DECISIONS})
        assert parse_decisions(raw) == _DECISIONS

    def test_parses_json_wrapped_in_text(self):
        raw = "```json\n" + json.dumps({"decisions": _DECISIONS}) + "\n```"
        assert parse_decisions(raw) == _DECISIONS

    def test_returns_none_for_garbage(self):
        assert parse_decisions("no json here") is None
        assert parse_decisions("") is None


class TestPortfolioFactCheck:
    # --- passing cases -----------------------------------------------------

    def test_passes_correct_category_count(self):
        ok, _ = _check("3 out of 5 SKUs are haircare (SKU2, SKU24, SKU68) — category is over-represented")
        assert ok is True

    def test_passes_correct_subset_count(self):
        ok, _ = _check("2 of 3 haircare SKUs (SKU2, SKU24) rely on Supplier 3")
        assert ok is True

    def test_passes_correct_supplier_reliance(self):
        ok, _ = _check(
            "2 SKUs (SKU2, SKU24) rely on Supplier 3 — a single delay hits both",
            "SKU16 and SKU34 depend on Supplier 1",
        )
        assert ok is True

    def test_passes_correct_shared_signal(self):
        ok, _ = _check("SKU2 and SKU24 share the same supplier and lead time of 21.4 days")
        assert ok is True

    def test_passes_number_words(self):
        ok, _ = _check("Two skincare SKUs (SKU16, SKU34) are on MONITOR")
        assert ok is True

    def test_ignores_non_count_numbers(self):
        ok, _ = _check("SKU68 needs 119 units at a lead time of 14.5 days; SKU2 needs 9 units")
        assert ok is True

    def test_without_decisions_only_checks_sku_count(self):
        raw = _insight("5 out of 6 SKUs are haircare", "SKU2 and SKU24")
        ok, _ = portfolio_output_guardrail(raw)
        assert ok is True

    # --- failing cases: the exact bad sentences from demo_output.json -------

    def test_fails_five_out_of_six_haircare(self):
        ok, msg = _check("5 out of 6 SKUs are haircare — category is over-represented in the at-risk portfolio")
        assert ok is False
        assert "5 out of 6 SKUs" in msg
        assert "haircare=3" in msg  # correction carries the real count

    def test_fails_sku68_shared_signals_with_supplier(self):
        ok, msg = _check(
            "SKU68 shares shared failure signals with Supplier 4 due to recent urgent reorders and lead time issues"
        )
        assert ok is False
        assert "SKU68" in msg and "at least 2 SKUs" in msg

    def test_fails_three_skus_rely_on_supplier_3(self):
        ok, msg = _check("3 SKUs (SKU2, SKU24, SKU68) rely on Supplier 3 — potential impact if this supplier fails")
        assert ok is False
        assert "SKU68 uses Supplier 4" in msg

    # --- failing cases: each check in isolation -----------------------------

    def test_fails_wrong_x_of_y_numerator(self):
        ok, msg = _check("2 of 5 SKUs are haircare (SKU2, SKU24, SKU68)")
        assert ok is False
        assert "2 of 5 SKUs" in msg

    def test_fails_wrong_n_skus_count(self):
        ok, msg = _check("4 skincare SKUs face supplier risk, led by SKU16 and SKU34")
        assert ok is False
        assert "4 skincare SKUs" in msg

    def test_fails_count_does_not_match_list(self):
        ok, msg = _check("3 SKUs (SKU2, SKU24) rely on Supplier 3")
        assert ok is False
        assert "does not match the SKUs listed" in msg

    def test_fails_unknown_sku(self):
        ok, msg = _check("SKU2 and SKU99 are both haircare")
        assert ok is False
        assert "SKU99" in msg

    def test_fails_wrong_supplier_reliance(self):
        ok, msg = _check("SKU16, SKU34 rely on Supplier 3")
        assert ok is False
        assert "SKU16 uses Supplier 1" in msg

    def test_checks_concentration_risks_and_summary(self):
        ok, msg = _check(
            "SKU2 and SKU24 rely on Supplier 3",
            summary="SKU2, SKU24 and SKU68 rely on Supplier 3.",
        )
        assert ok is False
        assert "SKU68 uses Supplier 4" in msg

    def test_checks_plain_text_output(self):
        raw = "### 3. Key Insights\n- 5 out of 6 SKUs are haircare\n- SKU2 and SKU24 rely on Supplier 3"
        ok, msg = portfolio_output_guardrail(_mock_output(raw), _DECISIONS)
        assert ok is False
        assert "5 out of 6" in msg

    # --- unit totals and inspection claims ------------------------------------

    def test_passes_correct_unit_total(self):
        ok, _ = _check("Supplier 3 covers 233 units across SKU2 and SKU24")
        assert ok is True

    def test_passes_single_sku_units(self):
        ok, _ = _check("SKU68 needs 119 units; SKU24 needs 224 units")
        assert ok is True

    def test_fails_wrong_unit_total(self):
        # Seen in a real run: Supplier 3's total is 9 + 224 = 233, not 91
        ok, msg = _check("2 of 5 SKUs recommended from Supplier 3 — risk affects 91 total units, SKU2 and SKU24")
        assert ok is False
        assert "91 total units" in msg

    def test_passes_correct_inspection_claim(self):
        ok, _ = _check("SKU2 and SKU24 share Pending inspections")
        assert ok is True

    def test_fails_wrong_inspection_claim(self):
        # Seen in a real run: SKU68's inspection result is Fail, not Pending
        ok, msg = _check("SKU68 and SKU2 share Pending inspections — potential quality issues")
        assert ok is False
        assert "SKU68 is Fail" in msg
