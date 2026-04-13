import pytest
import pandas as pd
from unittest.mock import patch

from src.tools import inventory_analysis_tool

# ---------------------------------------------------------------------------
# Mock data
# Designed so we can calculate expected values manually:
#
#   SKU0 (haircare):  sold=800, stock=5,   lead_time=30 → stock_needed≈800 → AT RISK
#   SKU1 (skincare):  sold=100, stock=50,  lead_time=5  → stock_needed≈16.7 → safe
#   SKU2 (haircare):  sold=10,  stock=100, lead_time=2  → stock_needed≈0.7  → safe
#   SKU3 (cosmetics): sold=500, stock=2,   lead_time=30 → stock_needed≈500  → AT RISK
#   SKU4 (skincare):  sold=50,  stock=0,   lead_time=10 → stock_needed≈16.7 → AT RISK + stockout
#
#   At-risk SKUs  : 3  (SKU0, SKU3, SKU4)
#   Stockout      : 1  (SKU4, stock=0)
#   revenue_density by at-risk SKU:
#       SKU3 = 6000 / (2+1) = 2000  ← highest
#       SKU0 = 8000 / (5+1) = 1333
#       SKU4 = 1000 / (0+1) = 1000
# ---------------------------------------------------------------------------

SAMPLE_DF = pd.DataFrame({
    "Product type":            ["haircare", "skincare", "haircare", "cosmetics", "skincare"],
    "SKU":                     ["SKU0",     "SKU1",     "SKU2",     "SKU3",      "SKU4"],
    "Number of products sold": [800,        100,        10,         500,         50],
    "Revenue generated":       [8000.0,     2000.0,     500.0,      6000.0,      1000.0],
    "Stock levels":            [5,          50,         100,        2,           0],
    "Lead time":               [30,         5,          2,          30,          10],
    "Supplier name":           ["Supplier A", "Supplier B", "Supplier C", "Supplier D", "Supplier E"],
})

# Access the raw function underneath the @tool decorator
_func = inventory_analysis_tool.func


@pytest.fixture(autouse=True)
def mock_csv():
    """Patch pd.read_csv in tools module so tests never touch the real file."""
    with patch("src.tools.pd.read_csv", return_value=SAMPLE_DF.copy()) as m:
        yield m


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_string(self):
        result = _func(product_type="all")
        assert isinstance(result, str)

    def test_not_empty(self):
        result = _func(product_type="all")
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

class TestHeader:
    def test_header_all(self):
        result = _func(product_type="all")
        assert "=== Inventory Analysis: ALL ===" in result

    def test_header_reflects_product_type(self):
        result = _func(product_type="skincare")
        assert "=== Inventory Analysis: SKINCARE ===" in result


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------

class TestSummary:
    def test_total_skus_all(self):
        result = _func(product_type="all")
        assert "Total SKUs analyzed: 5" in result

    def test_total_skus_haircare(self):
        result = _func(product_type="haircare")
        assert "Total SKUs analyzed: 2" in result

    def test_total_skus_skincare(self):
        result = _func(product_type="skincare")
        assert "Total SKUs analyzed: 2" in result

    def test_total_skus_cosmetics(self):
        result = _func(product_type="cosmetics")
        assert "Total SKUs analyzed: 1" in result

    def test_at_risk_count(self):
        result = _func(product_type="all")
        assert "At-risk SKUs: 3" in result

    def test_stockout_count(self):
        # SKU4 has stock=0
        result = _func(product_type="all")
        assert "Stockout (stock=0): 1" in result


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

class TestFiltering:
    def test_unknown_product_type_returns_zero_skus(self):
        result = _func(product_type="nonexistent")
        assert "Total SKUs analyzed: 0" in result

    def test_filter_case_insensitive(self):
        result_lower = _func(product_type="skincare")
        result_upper = _func(product_type="SKINCARE")
        assert "Total SKUs analyzed: 2" in result_lower
        assert "Total SKUs analyzed: 2" in result_upper

    def test_filtered_result_excludes_other_types(self):
        result = _func(product_type="cosmetics")
        assert "SKU0" not in result   # haircare — should be excluded
        assert "SKU1" not in result   # skincare — should be excluded


# ---------------------------------------------------------------------------
# Stockout risk logic
# ---------------------------------------------------------------------------

class TestStockoutRisk:
    def test_at_risk_skus_appear_in_output(self):
        result = _func(product_type="all")
        # SKU0 and SKU3 have highest revenue_density among at-risk SKUs
        assert "SKU0" in result
        assert "SKU3" in result

    def test_safe_skus_not_in_high_priority(self):
        result = _func(product_type="all")
        # SKU1 and SKU2 are safe — should not appear in High Priority section
        high_priority_section = result.split("Top 5 High Priority SKUs")[1]
        assert "SKU1" not in high_priority_section
        assert "SKU2" not in high_priority_section

    def test_high_priority_section_present(self):
        result = _func(product_type="all")
        assert "Top 5 High Priority SKUs" in result

    def test_stockout_sku_appears_in_output(self):
        # SKU4 has stock=0 and is at risk → should appear
        result = _func(product_type="all")
        assert "SKU4" in result

    def test_stock_value_shown_correctly(self):
        result = _func(product_type="all")
        # SKU3 has stock=2
        assert "Stock=2" in result


# ---------------------------------------------------------------------------
# CSV file path
# ---------------------------------------------------------------------------

class TestCSVPath:
    def test_reads_from_correct_path(self, mock_csv):
        _func(product_type="all")
        mock_csv.assert_called_once_with("data/supply_chain_data.csv")

    def test_reads_csv_once_per_call(self, mock_csv):
        _func(product_type="all")
        _func(product_type="skincare")
        assert mock_csv.call_count == 2
