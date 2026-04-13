import math
import pandas as pd
from crewai.tools import tool

DATA_PATH = "data/supply_chain_data.csv"

@tool("Inventory Analysis Tool")
def inventory_analysis_tool(product_type: str = "all") -> str:
    """
    Analyzes inventory data from the supply chain dataset.
    Returns stock levels, demand, and revenue density for each SKU.
    Use product_type to filter: 'haircare', 'skincare', 'cosmetics', or 'all'
    """
    df = pd.read_csv(DATA_PATH)

    if product_type != "all":
        df = df[df["Product type"].str.lower() == product_type.lower()]

    df["daily_demand"] = (df["Number of products sold"] / 30).round(2)
    df["days_of_stock"] = (df["Stock levels"] / (df["daily_demand"] + 0.01)).round(1)
    df["stock_needed"] = (df["daily_demand"] * df["Lead time"]).round(1)
    df["stockout_risk"] = df["Stock levels"] < df["stock_needed"]
    df["revenue_density"] = (df["Revenue generated"] / (df["Stock levels"] + 1)).round(0)

    result = []
    result.append(f"=== Inventory Analysis: {product_type.upper()} ===\n")
    result.append(f"Total SKUs analyzed: {len(df)}")
    result.append(f"At-risk SKUs: {df['stockout_risk'].sum()}")
    result.append(f"Stockout (stock=0): {(df['Stock levels'] == 0).sum()}\n")

    high_risk = df[df["stockout_risk"]].nlargest(5, "revenue_density")
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
    df = pd.read_csv(DATA_PATH)

    if sku_list.strip().lower() != "all":
        skus = [s.strip() for s in sku_list.split(",")]
        df = df[df["SKU"].isin(skus)]

    def _score(row) -> int:
        score = 0
        if row["Lead time"] > 20:
            score += 2
        elif row["Lead time"] > 10:
            score += 1
        if row["Defect rates"] > 4.0:
            score += 2
        elif row["Defect rates"] > 2.0:
            score += 1
        inspection = str(row["Inspection results"]).strip().lower()
        if inspection == "fail":
            score += 3
        elif inspection == "pending":
            score += 1
        return score

    def _level(score: int) -> str:
        if score >= 5:
            return "Critical"
        if score >= 3:
            return "High"
        return "Medium"

    df["risk_score"] = df.apply(_score, axis=1)
    df["risk_level"] = df["risk_score"].apply(_level)

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
            reasons = []
            if row["Lead time"] > 10:
                reasons.append(f"lead_time={int(row['Lead time'])}d")
            if row["Defect rates"] > 2.0:
                reasons.append(f"defect={row['Defect rates']:.1f}%")
            inspection = str(row["Inspection results"]).strip()
            if inspection.lower() in ("fail", "pending"):
                reasons.append(f"inspection={inspection}")
            result.append(
                f"  - {row['SKU']} ({row['Product type']}): "
                f"Score={int(row['risk_score'])}, "
                f"Supplier={row['Supplier name']}, "
                f"Flags=[{', '.join(reasons)}]"
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
    df = pd.read_csv(DATA_PATH)

    # --- Locate the requested SKU ---
    sku_row = df[df["SKU"] == sku]
    if sku_row.empty:
        return f"SKU '{sku}' not found in dataset."

    sku_data         = sku_row.iloc[0]
    product_type     = sku_data["Product type"]
    current_supplier = sku_data["Supplier name"]
    stock_level      = int(sku_data["Stock levels"])

    # Demand data
    daily_demand  = round(sku_data["Number of products sold"] / 30, 2)
    days_of_stock = round(stock_level / (daily_demand + 0.01), 1)

    # --- Step 1: Determine action first — this drives weight selection ---
    # stock=0 or Critical → must restock immediately regardless of other factors
    # High → restock this week, balance speed and quality
    # Medium → no immediate pressure, optimise for long-term cost
    normalized_risk = risk_level.strip().title()
    if stock_level == 0 or normalized_risk == "Critical":
        action = "URGENT_REORDER"
    elif normalized_risk == "High":
        action = "REORDER"
    else:
        action = "MONITOR"

    # --- Step 2: Select composite score weights based on action ---
    # weights = (lead_time, defect_rate, manufacturing_cost)
    _WEIGHT_PROFILES = {
        "URGENT_REORDER": (0.70, 0.20, 0.10),
        "REORDER":        (0.40, 0.40, 0.20),
        "MONITOR":        (0.20, 0.30, 0.50),
    }
    w_lead, w_defect, w_cost = _WEIGHT_PROFILES[action]

    # --- Step 3: Aggregate suppliers for this product type ---
    same_type = df[df["Product type"] == product_type]
    supplier_stats = (
        same_type
        .groupby("Supplier name")
        .agg(
            avg_lead_time         =("Lead time",           "mean"),
            avg_defect_rate       =("Defect rates",        "mean"),
            avg_manufacturing_cost=("Manufacturing costs", "mean"),
            sku_count             =("SKU",                 "count"),
        )
        .round(2)
        .reset_index()
    )

    # --- Step 4: Normalise each metric to [0, 1] then apply action weights ---
    # Lower is better for all three metrics.
    for col in ["avg_lead_time", "avg_defect_rate", "avg_manufacturing_cost"]:
        lo, hi = supplier_stats[col].min(), supplier_stats[col].max()
        if hi != lo:
            supplier_stats[f"{col}_norm"] = (supplier_stats[col] - lo) / (hi - lo)
        else:
            supplier_stats[f"{col}_norm"] = 0.0

    supplier_stats["composite_score"] = (
        supplier_stats["avg_lead_time_norm"]          * w_lead  +
        supplier_stats["avg_defect_rate_norm"]         * w_defect +
        supplier_stats["avg_manufacturing_cost_norm"]  * w_cost
    ).round(3)

    supplier_stats = supplier_stats.sort_values("composite_score").reset_index(drop=True)

    best_row       = supplier_stats.iloc[0]
    best_supplier  = best_row["Supplier name"]
    best_lead_time = best_row["avg_lead_time"]

    # --- Step 5: Calculate order quantity using best supplier's lead time ---
    # Formula: ceil(daily_demand × lead_time × 1.5 safety buffer)
    order_quantity = math.ceil(daily_demand * best_lead_time * 1.5)

    # --- Build report ---
    weight_label = (
        f"lead time {int(w_lead*100)}% | "
        f"defect rate {int(w_defect*100)}% | "
        f"manufacturing cost {int(w_cost*100)}%"
    )

    result = []
    result.append(f"=== Supplier Comparison: {sku} ({product_type}) ===\n")
    result.append(f"Current supplier : {current_supplier}")
    result.append(f"Stock level      : {stock_level} units")
    result.append(f"Daily demand     : {daily_demand} units/day")
    result.append(f"Days of stock    : {days_of_stock} days")
    result.append(f"Risk level input : {normalized_risk}\n")

    result.append("--- RECOMMENDATION (use these exact values) ---")
    result.append(f"Action           : {action}")
    result.append(f"Recommended      : {best_supplier}")
    result.append(
        f"Order quantity   : {order_quantity} units"
        f"  [calc: ceil({daily_demand} × {best_lead_time}d × 1.5)]"
    )
    result.append("")

    result.append(f"Supplier ranking (composite score — lower = better):")
    result.append(f"Weights ({action}): {weight_label}\n")

    for rank, row in supplier_stats.iterrows():
        tags = []
        if row["Supplier name"] == current_supplier:
            tags.append("CURRENT")
        if rank == 0 and row["Supplier name"] != current_supplier:
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

    rejected = [r["Supplier name"] for _, r in supplier_stats.iterrows() if r["Supplier name"] != best_supplier]
    result.append(f"\nRejected suppliers: {', '.join(rejected)}")
    return "\n".join(result)