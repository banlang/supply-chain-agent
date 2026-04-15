"""
Tests for src/schemas.py — Pydantic model validation.

Confirms that Literal constraints, Field validators (gt, ge, le, min_length)
and required fields are enforced at construction time.
"""
import pytest
from pydantic import ValidationError

from src.schemas import (
    SKURisk,
    RiskDetectionOutput,
    ReorderDecision,
    ReorderReport,
    PortfolioInsight,
)


# ---------------------------------------------------------------------------
# SKURisk
# ---------------------------------------------------------------------------

class TestSKURisk:
    def _valid(self, **kwargs):
        defaults = dict(
            sku="SKU68", product_type="haircare",
            risk_level="High", score=3,
            supplier="Supplier 2", flags=["inspection=Fail"],
        )
        defaults.update(kwargs)
        return SKURisk(**defaults)

    def test_valid_high(self):
        s = self._valid(risk_level="High", score=3)
        assert s.risk_level == "High"

    def test_valid_critical(self):
        s = self._valid(risk_level="Critical", score=5)
        assert s.risk_level == "Critical"

    def test_valid_medium(self):
        s = self._valid(risk_level="Medium", score=1)
        assert s.risk_level == "Medium"

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(risk_level="Very High")

    def test_score_above_max_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(score=8)   # max is 7

    def test_score_below_min_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(score=-1)  # min is 0

    def test_empty_flags_allowed(self):
        s = self._valid(flags=[])
        assert s.flags == []


# ---------------------------------------------------------------------------
# ReorderDecision
# ---------------------------------------------------------------------------

class TestReorderDecision:
    def _valid(self, **kwargs):
        defaults = dict(
            sku="SKU2", product_type="haircare",
            action="REORDER", recommended_supplier="Supplier 3",
            order_quantity=9, reason="High risk.", rejected_suppliers=[],
        )
        defaults.update(kwargs)
        return ReorderDecision(**defaults)

    def test_valid_reorder(self):
        d = self._valid()
        assert d.action == "REORDER"
        assert d.order_quantity == 9

    def test_all_valid_actions(self):
        for action in ("URGENT_REORDER", "REORDER", "MONITOR"):
            d = self._valid(action=action)
            assert d.action == action

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(action="CRITICAL_REORDER")

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(order_quantity=0)

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(order_quantity=-5)

    def test_positive_quantity_accepted(self):
        d = self._valid(order_quantity=1)
        assert d.order_quantity == 1

    def test_rejected_suppliers_list(self):
        d = self._valid(rejected_suppliers=["Supplier 1", "Supplier 2"])
        assert len(d.rejected_suppliers) == 2


# ---------------------------------------------------------------------------
# ReorderReport
# ---------------------------------------------------------------------------

class TestReorderReport:
    def test_empty_decisions_allowed(self):
        r = ReorderReport(decisions=[])
        assert r.decisions == []

    def test_multiple_decisions(self):
        d = ReorderDecision(
            sku="SKU2", product_type="haircare", action="REORDER",
            recommended_supplier="Supplier 3", order_quantity=9,
            reason="High risk.", rejected_suppliers=[],
        )
        r = ReorderReport(decisions=[d, d])
        assert len(r.decisions) == 2


# ---------------------------------------------------------------------------
# PortfolioInsight
# ---------------------------------------------------------------------------

class TestPortfolioInsight:
    def _valid(self, **kwargs):
        defaults = dict(
            patterns=["3 of 5 SKUs are haircare — category over-represented"],
            concentration_risks=["Supplier 3 recommended for 2 REORDER SKUs"],
            executive_summary="Portfolio risk is concentrated in haircare.",
        )
        defaults.update(kwargs)
        return PortfolioInsight(**defaults)

    def test_valid(self):
        p = self._valid()
        assert len(p.patterns) == 1

    def test_empty_patterns_rejected(self):
        with pytest.raises(ValidationError):
            self._valid(patterns=[])

    def test_empty_concentration_risks_allowed(self):
        p = self._valid(concentration_risks=[])
        assert p.concentration_risks == []

    def test_multiple_patterns(self):
        p = self._valid(patterns=["Pattern A", "Pattern B", "Pattern C"])
        assert len(p.patterns) == 3

    def test_missing_executive_summary_rejected(self):
        with pytest.raises(ValidationError):
            PortfolioInsight(
                patterns=["A pattern"],
                concentration_risks=[],
            )
