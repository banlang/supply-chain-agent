"""
Tests for src/guardrails.py

inventory_output_guardrail  — rejects hallucinated/missing inventory data
risk_output_guardrail       — rejects invented risk scale labels
reorder_output_guardrail    — validates supplier, action, qty, zero-stock rule
portfolio_output_guardrail  — requires cross-SKU references (≥ 2 distinct SKUs)

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
