import pandas as pd  # noqa: F401 — tests patch src.tools.custom_tool.pd.read_csv
from pathlib import Path
from crewai.tools import tool

from src.analytics import (
    load_dataset, compute_inventory, top_priority_skus,
    compute_risk, risk_flags, compare_suppliers,
)

# Resolve path relative to this file so it works regardless of working directory
# src/tools/custom_tool.py -> src/tools/ -> src/ -> project root
_DATA_PATH = str(Path(__file__).parent.parent.parent / "data" / "supply_chain_data.csv")

# The deterministic logic lives in src/analytics.py (shared with the Streamlit UI).
# These tools only format its results as text for the agents.


@tool("Inventory Analysis Tool")
def inventory_analysis_tool(product_type: str = "all") -> str:
    """
    Analyzes inventory data from the supply chain dataset.
    Returns stock levels, demand, and revenue density for each SKU.
    Use product_type to filter: 'haircare', 'skincare', 'cosmetics', or 'all'
    """
    df = compute_inventory(load_dataset(_DATA_PATH), product_type)

    result = []
    result.append(f"=== Inventory Analysis: {product_type.upper()} ===\n")
    result.append(f"Total SKUs analyzed: {len(df)}")
    result.append(f"At-risk SKUs: {df['stockout_risk'].sum()}")
    result.append(f"Stockout (stock=0): {(df['Stock levels'] == 0).sum()}\n")

    high_risk = top_priority_skus(df, 5)
    result.append("Top 5 High Priority SKUs (high revenue + low stock):")
    for _, row in high_risk.iterrows():
        result.append(
            f"  - {row['SKU']} ({row['Product type']}): "
            f"Stock={int(row['Stock levels'])}, "
            f"Days left={row['days_of_stock']}, "
            f"Revenue=${row['Revenue generated']:.0f}, "
            f"Supplier={row['Supplier name']}"
        )

    return "\n".join(result)


@tool("Risk Detection Tool")
def risk_detection_tool(sku_list: str = "all") -> str:
    """
    Analyzes supplier and quality risk for specific SKUs using lead time,
    defect rates, and inspection results. Assigns a risk level to each SKU:
      Critical (score 5-7), High (score 3-4), Medium (score 0-2).
    Scoring: lead_time>20 +2, >10 +1 | defect_rate>4% +2, >2% +1 |
             inspection=Fail +3, Pending +1.
    Pass sku_list as comma-separated SKU codes e.g. "SKU0,SKU5,SKU42",
    or "all" to analyze every SKU in the dataset.
    """
    df = compute_risk(load_dataset(_DATA_PATH), sku_list)

    result = []
    result.append("=== Risk Detection Report ===\n")
    result.append(f"SKUs analyzed: {len(df)}")
    result.append(
        f"Critical: {(df['risk_level'] == 'Critical').sum()}  "
        f"High: {(df['risk_level'] == 'High').sum()}  "
        f"Medium: {(df['risk_level'] == 'Medium').sum()}\n"
    )

    for level in ["Critical", "High", "Medium"]:
        subset = df[df["risk_level"] == level].sort_values("risk_score", ascending=False)
        if subset.empty:
            continue
        result.append(f"{level.upper()} RISK ({len(subset)} SKUs):")
        for _, row in subset.iterrows():
            result.append(
                f"  - {row['SKU']} ({row['Product type']}): "
                f"Score={int(row['risk_score'])}, "
                f"Supplier={row['Supplier name']}, "
                f"Flags=[{', '.join(risk_flags(row))}]"
            )
        result.append("")

    return "\n".join(result)


@tool("Supplier Comparison Tool")
def supplier_comparison_tool(sku: str, risk_level: str = "Medium") -> str:
    """
    Compares all available suppliers for the product type of a given SKU.
    Aggregates mean lead time, defect rates, and manufacturing costs per supplier,
    then ranks them using a composite score whose weights shift based on urgency:
      URGENT_REORDER — lead time 70% / defect 20% / cost 10%  (speed first)
      REORDER        — lead time 40% / defect 40% / cost 20%  (balanced)
      MONITOR        — lead time 20% / defect 30% / cost 50%  (cost efficiency)

    Action is determined first (from stock level + risk_level), then the matching
    weights are applied before selecting the best supplier.

    Pass a single SKU code e.g. "SKU3" and the risk level from the Risk Analyst
    e.g. risk_level="Critical", "High", or "Medium" (default: "Medium").
    """
    c = compare_suppliers(load_dataset(_DATA_PATH), sku, risk_level)
    if c is None:
        return f"SKU '{sku}' not found in dataset."

    w_lead, w_defect, w_cost = c["weights"]
    action, best_supplier = c["action"], c["best_supplier"]

    # --- Build report ---
    weight_label = (
        f"lead time {int(w_lead*100)}% | "
        f"defect rate {int(w_defect*100)}% | "
        f"manufacturing cost {int(w_cost*100)}%"
    )

    result = []
    result.append(f"=== Supplier Comparison: {sku} ({c['product_type']}) ===\n")
    result.append(f"Current supplier : {c['current_supplier']}")
    result.append(f"Stock level      : {c['stock_level']} units")
    result.append(f"Daily demand     : {c['daily_demand']} units/day")
    result.append(f"Days of stock    : {c['days_of_stock']} days")
    result.append(f"Risk level input : {c['normalized_risk']}\n")

    result.append("--- RECOMMENDATION (use these exact values) ---")
    result.append(f"Action           : {action}")
    result.append(f"Recommended      : {best_supplier}")
    result.append(
        f"Order quantity   : {c['order_quantity']} units"
        f"  [calc: ceil({c['daily_demand']} × {c['best_lead_time']}d × 1.5)]"
    )
    result.append("")

    result.append(f"Supplier ranking (composite score — lower = better):")
    result.append(f"Weights ({action}): {weight_label}\n")

    for rank, row in c["ranking"].iterrows():
        tags = []
        if row["Supplier name"] == c["current_supplier"]:
            tags.append("CURRENT")
        if rank == 0 and row["Supplier name"] != c["current_supplier"]:
            tags.append("RECOMMENDED")
        elif rank == 0:
            tags.append("RECOMMENDED (same as current)")
        label = f"  <- {', '.join(tags)}" if tags else ""
        result.append(
            f"  #{rank + 1} {row['Supplier name']}{label}\n"
            f"      Lead time: {row['avg_lead_time']}d | "
            f"Defect: {row['avg_defect_rate']}% | "
            f"Mfg cost: ${row['avg_manufacturing_cost']} | "
            f"Score: {row['composite_score']}"
        )

    result.append(f"\nRejected suppliers: {', '.join(c['rejected'])}")
    return "\n".join(result)
