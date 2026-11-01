"""
Tests for src/analytics.py

1. Refactor safety: every tool output is byte-identical to the output captured
   from the pre-refactor tools (tests/fixtures/tool_outputs_golden.json —
   all 100 SKUs × 4 risk inputs, plus inventory and risk variants).
2. Timing (days_of_cover / gap_days), portfolio KPIs, supplier share.
"""
import json
import math
from pathlib import Path

import pytest

from src.analytics import (
    ACTION_NOW, compare_suppliers, compute_inventory, determine_action, load_dataset,
    portfolio_kpis, supplier_lead_time, timing, top_supplier_share,
)
from src.tools import inventory_analysis_tool, risk_detection_tool, supplier_comparison_tool

DEMO_JSON = Path(__file__).parent.parent / "demo_output.json"
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "tool_outputs_golden.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def df():
    return load_dataset()


# ---------------------------------------------------------------------------
# Tool outputs unchanged by the refactor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("product_type", list(GOLDEN["inventory"]))
def test_inventory_tool_output_unchanged(product_type):
    assert inventory_analysis_tool.func(product_type) == GOLDEN["inventory"][product_type]


@pytest.mark.parametrize("sku_list", list(GOLDEN["risk"]))
def test_risk_tool_output_unchanged(sku_list):
    assert risk_detection_tool.func(sku_list) == GOLDEN["risk"][sku_list]


def test_supplier_tool_output_unchanged_for_every_sku_and_risk_level():
    mismatches = [
        key for key, expected in GOLDEN["supplier"].items()
        if supplier_comparison_tool.func(*key.split("|")) != expected
    ]
    assert len(GOLDEN["supplier"]) == 404
    assert mismatches == []


# ---------------------------------------------------------------------------
# Supplier comparison
# ---------------------------------------------------------------------------

class TestCompareSuppliers:
    def test_demo_decisions_reproduced(self, df):
        # The 5 decisions in the demo run, recomputed without the LLM
        expected = {
            ("SKU68", "High"):   ("URGENT_REORDER", "Supplier 4", 119),
            ("SKU2", "High"):    ("REORDER", "Supplier 3", 9),
            ("SKU24", "High"):   ("REORDER", "Supplier 3", 224),
            ("SKU16", "Medium"): ("MONITOR", "Supplier 1", 186),
            ("SKU34", "Medium"): ("MONITOR", "Supplier 1", 400),
        }
        for (sku, level), want in expected.items():
            c = compare_suppliers(df, sku, level)
            assert (c["action"], c["best_supplier"], c["order_quantity"]) == want

    def test_unknown_sku_returns_none(self, df):
        assert compare_suppliers(df, "SKU999") is None

    def test_exclude_removes_supplier_and_keeps_action(self, df):
        base = compare_suppliers(df, "SKU68", "High")
        new = compare_suppliers(df, "SKU68", "High", exclude=["Supplier 4"])
        assert new["action"] == base["action"] == "URGENT_REORDER"
        assert "Supplier 4" not in list(new["ranking"]["Supplier name"])
        assert new["best_supplier"] != "Supplier 4"
        assert new["excluded"] == ["Supplier 4"]

    def test_excluding_every_supplier_returns_none(self, df):
        all_suppliers = list(df["Supplier name"].unique())
        assert compare_suppliers(df, "SKU68", "High", exclude=all_suppliers) is None

    @pytest.mark.parametrize("stock,level,action", [
        (0, "Medium", "URGENT_REORDER"), (5, " critical ", "URGENT_REORDER"),
        (5, "High", "REORDER"), (5, "Medium", "MONITOR"), (5, "unknown", "MONITOR"),
    ])
    def test_determine_action(self, stock, level, action):
        assert determine_action(stock, level) == action


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

class TestTiming:
    def test_in_time(self):
        t = timing(stock=100, demand=5, lead_time=12)
        assert t["days_of_cover"] == 20
        assert t["gap_days"] == 8
        assert t["in_time"] is True
        assert t["status"] == "In time: order within 8.0 days"

    def test_late(self):
        t = timing(stock=0, demand=5.43, lead_time=14.5)
        assert t["gap_days"] == -14.5
        assert t["in_time"] is False
        assert t["status"] == "LATE by 14.5 days — stock runs out before delivery"

    def test_exactly_zero_gap_is_late(self):
        assert timing(stock=10, demand=1, lead_time=10)["in_time"] is False

    def test_zero_demand_never_runs_out(self):
        t = timing(stock=10, demand=0, lead_time=10)
        assert math.isinf(t["days_of_cover"]) and t["in_time"] is True

    def test_unknown_lead_time(self):
        t = timing(stock=10, demand=1, lead_time=None)
        assert t["in_time"] is None and t["gap_days"] is None

    def test_returns_plain_python_types(self, df):
        row = df[df["SKU"] == "SKU2"].iloc[0]
        t = timing(row["Stock levels"], 0.27, supplier_lead_time(df, "haircare", "Supplier 3"))
        assert type(t["in_time"]) is bool and type(t["gap_days"]) is float

    def test_sku16_and_sku34_have_under_one_day_of_cover(self, df):
        # Backs the README limitation: MONITOR SKUs with < 1 day of cover
        for sku, demand in [("SKU16", 9.33), ("SKU34", 20.07)]:
            stock = df.loc[df["SKU"] == sku, "Stock levels"].iloc[0]
            assert timing(stock, demand, 13.27)["days_of_cover"] < 1

    def test_supplier_lead_time_matches_comparison_table(self, df):
        c = compare_suppliers(df, "SKU68", "High")
        assert supplier_lead_time(df, "haircare", "Supplier 4") == c["best_lead_time"] == 14.5
        assert supplier_lead_time(df, "haircare", "Supplier 99") is None


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

class TestPortfolioKpis:
    def test_real_dataset_counts(self, df):
        k = portfolio_kpis(df)
        assert (k["at_risk_skus"], k["total_skus"], k["zero_stock_skus"]) == (85, 100, 1)
        assert 0 < k["at_risk_revenue"] < k["total_revenue"]

    def test_at_risk_revenue_is_sum_of_revenue_field(self, df):
        inv = compute_inventory(df)
        expected = inv.loc[inv["stockout_risk"], "Revenue generated"].sum()
        assert portfolio_kpis(df)["at_risk_revenue"] == pytest.approx(expected)

    def test_365_day_assumption_gives_27_at_risk(self, df, monkeypatch):
        # Backs the README limitation sentence: 85 → 27 of 100
        monkeypatch.setattr("src.analytics.DEMAND_PERIOD_DAYS", 365)
        assert portfolio_kpis(df)["at_risk_skus"] == 27


class TestTopSupplierShare:
    DECISIONS = [
        {"action": "REORDER",        "recommended_supplier": "Supplier 3", "order_quantity": 9},
        {"action": "REORDER",        "recommended_supplier": "Supplier 3", "order_quantity": 224},
        {"action": "URGENT_REORDER", "recommended_supplier": "Supplier 4", "order_quantity": 119},
        {"action": "MONITOR",        "recommended_supplier": "Supplier 1", "order_quantity": 186},
        {"action": "MONITOR",        "recommended_supplier": "Supplier 1", "order_quantity": 400},
    ]

    def test_share_by_units(self):
        s = top_supplier_share(self.DECISIONS)
        assert s["supplier"] == "Supplier 1"
        assert (s["units"], s["total_units"]) == (586, 938)
        assert s["share"] == pytest.approx(586 / 938)
        assert (s["decisions"], s["total_decisions"]) == (2, 5)

    def test_share_of_orders_needing_action_now(self):
        s = top_supplier_share(self.DECISIONS, actions=ACTION_NOW)
        assert s["supplier"] == "Supplier 3"
        assert (s["units"], s["total_units"]) == (233, 352)
        assert (s["decisions"], s["total_decisions"]) == (2, 3)

    def test_empty(self):
        assert top_supplier_share([]) is None
        monitor_only = [d for d in self.DECISIONS if d["action"] == "MONITOR"]
        assert top_supplier_share(monitor_only, actions=ACTION_NOW) is None

    @pytest.mark.skipif(not DEMO_JSON.exists(), reason="demo_output.json not generated (python demo_output.py)")
    def test_pinned_numbers_from_demo_output(self):
        # Pins the Executive view figures for the current demo run
        decisions = json.loads(DEMO_JSON.read_text(encoding="utf-8"))["decisions"]
        now = top_supplier_share(decisions, actions=ACTION_NOW)
        assert (now["supplier"], now["units"], now["total_units"]) == ("Supplier 3", 233, 352)
        assert f"{now['share']:.0%}" == "66%"
        everything = top_supplier_share(decisions)
        assert (everything["supplier"], everything["units"], everything["total_units"]) == ("Supplier 1", 586, 938)
        assert f"{everything['share']:.0%}" == "62%"
