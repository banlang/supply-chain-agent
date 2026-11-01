"""
Deterministic supply-chain logic shared by the CrewAI tools and the Streamlit UI.

Every function here is pure (DataFrame in, data out) — no LLM, no I/O except
load_dataset(). The @tool wrappers in src/tools/custom_tool.py only format
these results as text, so tool outputs are unchanged by this refactor
(proven by tests/test_analytics.py against a pre-refactor golden file).
"""
import math
from pathlib import Path

import pandas as pd

# src/analytics.py -> src/ -> project root
DATA_PATH = str(Path(__file__).parent.parent / "data" / "supply_chain_data.csv")

# daily_demand = "Number of products sold" / DEMAND_PERIOD_DAYS.
# Assumption: the dataset does not state the sales period.
DEMAND_PERIOD_DAYS = 30

# weights = (lead_time, defect_rate, manufacturing_cost)
WEIGHT_PROFILES = {
    "URGENT_REORDER": (0.70, 0.20, 0.10),
    "REORDER":        (0.40, 0.40, 0.20),
    "MONITOR":        (0.20, 0.30, 0.50),
}

ACTION_ORDER = ["URGENT_REORDER", "REORDER", "MONITOR"]


def load_dataset(path: str = DATA_PATH) -> pd.DataFrame:
    return pd.read_csv(path)


def daily_demand(products_sold: float) -> float:
    return round(products_sold / DEMAND_PERIOD_DAYS, 2)


# ---------------------------------------------------------------------------
# Inventory (Agent 1)
# ---------------------------------------------------------------------------

def compute_inventory(df: pd.DataFrame, product_type: str = "all") -> pd.DataFrame:
    """Add daily_demand, days_of_stock, stock_needed, stockout_risk, revenue_density."""
    df = df.copy()
    if product_type != "all":
        df = df[df["Product type"].str.lower() == product_type.lower()]

    df["daily_demand"] = (df["Number of products sold"] / DEMAND_PERIOD_DAYS).round(2)
    df["days_of_stock"] = (df["Stock levels"] / (df["daily_demand"] + 0.01)).round(1)
    df["stock_needed"] = (df["daily_demand"] * df["Lead time"]).round(1)
    df["stockout_risk"] = df["Stock levels"] < df["stock_needed"]
    df["revenue_density"] = (df["Revenue generated"] / (df["Stock levels"] + 1)).round(0)
    return df


def top_priority_skus(inventory: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """At-risk SKUs ranked by revenue density (highest first)."""
    return inventory[inventory["stockout_risk"]].nlargest(n, "revenue_density")


# ---------------------------------------------------------------------------
# Risk (Agent 2)
# ---------------------------------------------------------------------------

def risk_score(row) -> int:
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


def risk_level(score: int) -> str:
    if score >= 5:
        return "Critical"
    if score >= 3:
        return "High"
    return "Medium"


def risk_flags(row) -> list[str]:
    reasons = []
    if row["Lead time"] > 10:
        reasons.append(f"lead_time={int(row['Lead time'])}d")
    if row["Defect rates"] > 2.0:
        reasons.append(f"defect={row['Defect rates']:.1f}%")
    inspection = str(row["Inspection results"]).strip()
    if inspection.lower() in ("fail", "pending"):
        reasons.append(f"inspection={inspection}")
    return reasons


def compute_risk(df: pd.DataFrame, sku_list: str = "all") -> pd.DataFrame:
    """Add risk_score and risk_level for the requested SKUs (comma-separated or 'all')."""
    df = df.copy()
    if sku_list.strip().lower() != "all":
        skus = [s.strip() for s in sku_list.split(",")]
        df = df[df["SKU"].isin(skus)]

    df["risk_score"] = df.apply(risk_score, axis=1)
    df["risk_level"] = df["risk_score"].apply(risk_level)
    return df


# ---------------------------------------------------------------------------
# Supplier comparison (Agent 3)
# ---------------------------------------------------------------------------

def determine_action(stock_level: int, risk_level_input: str) -> str:
    """
    stock=0 or Critical → must restock immediately regardless of other factors
    High → restock this week, balance speed and quality
    Medium → no immediate pressure, optimise for long-term cost
    """
    normalized_risk = risk_level_input.strip().title()
    if stock_level == 0 or normalized_risk == "Critical":
        return "URGENT_REORDER"
    if normalized_risk == "High":
        return "REORDER"
    return "MONITOR"


def compare_suppliers(
    df: pd.DataFrame, sku: str, risk_level_input: str = "Medium", exclude=()
) -> dict | None:
    """
    Rank suppliers for a SKU's product type. Returns None if the SKU is unknown.

    exclude: supplier names to leave out before ranking (used by the UI's
    "recompute with constraint" reject path; the tools never pass it).
    """
    sku_row = df[df["SKU"] == sku]
    if sku_row.empty:
        return None

    sku_data         = sku_row.iloc[0]
    product_type     = sku_data["Product type"]
    stock_level      = int(sku_data["Stock levels"])
    demand           = daily_demand(sku_data["Number of products sold"])
    normalized_risk  = risk_level_input.strip().title()

    # Step 1: action first — this drives weight selection
    action = determine_action(stock_level, risk_level_input)
    w_lead, w_defect, w_cost = WEIGHT_PROFILES[action]

    # Step 2: aggregate suppliers for this product type
    same_type = df[(df["Product type"] == product_type) & ~df["Supplier name"].isin(list(exclude))]
    if same_type.empty:
        return None
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

    # Step 3: normalise each metric to [0, 1] (lower is better), apply weights
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

    # Step 4: ceil(daily_demand × lead_time × 1.5 safety buffer)
    order_quantity = math.ceil(demand * best_lead_time * 1.5)

    return {
        "sku":              sku,
        "product_type":     product_type,
        "current_supplier": sku_data["Supplier name"],
        "stock_level":      stock_level,
        "daily_demand":     demand,
        "days_of_stock":    round(stock_level / (demand + 0.01), 1),
        "normalized_risk":  normalized_risk,
        "action":           action,
        "weights":          (w_lead, w_defect, w_cost),
        "ranking":          supplier_stats,
        "best_supplier":    best_supplier,
        "best_lead_time":   best_lead_time,
        "order_quantity":   order_quantity,
        "rejected":         [s for s in supplier_stats["Supplier name"] if s != best_supplier],
        "excluded":         list(exclude),
    }


# ---------------------------------------------------------------------------
# Timing (display only — does not change any action)
# ---------------------------------------------------------------------------

def supplier_lead_time(df: pd.DataFrame, product_type: str, supplier: str) -> float | None:
    """Mean lead time of a supplier across SKUs of a product type (same basis as compare_suppliers)."""
    rows = df[(df["Product type"] == product_type) & (df["Supplier name"] == supplier)]
    if rows.empty:
        return None
    return round(float(rows["Lead time"].mean()), 2)


def timing(stock: float, demand: float, lead_time: float | None) -> dict:
    """
    days_of_cover = stock / daily_demand
    gap_days      = days_of_cover - lead_time
    gap > 0 → in time (order within gap days); otherwise late.
    """
    # plain floats/bools — callers compare with `is False`, which numpy scalars fail
    days_of_cover = math.inf if demand <= 0 else float(stock) / float(demand)
    if lead_time is None:
        return {"days_of_cover": days_of_cover, "lead_time": None, "gap_days": None,
                "in_time": None, "status": "Lead time unknown"}
    gap = days_of_cover - float(lead_time)
    if gap > 0:
        status = f"In time: order within {gap:.1f} days"
    else:
        status = f"LATE by {abs(gap):.1f} days — stock runs out before delivery"
    return {"days_of_cover": days_of_cover, "lead_time": lead_time, "gap_days": gap,
            "in_time": bool(gap > 0), "status": status}


# ---------------------------------------------------------------------------
# Portfolio KPIs (executive view)
# ---------------------------------------------------------------------------

def portfolio_kpis(df: pd.DataFrame) -> dict:
    inv = compute_inventory(df)
    at_risk = inv[inv["stockout_risk"]]
    return {
        "total_skus":          len(inv),
        "at_risk_skus":        int(inv["stockout_risk"].sum()),
        "zero_stock_skus":     int((inv["Stock levels"] == 0).sum()),
        "at_risk_revenue":     float(at_risk["Revenue generated"].sum()),
        "total_revenue":       float(inv["Revenue generated"].sum()),
    }


ACTION_NOW = ("URGENT_REORDER", "REORDER")


def top_supplier_share(decisions: list[dict], actions=None) -> dict | None:
    """
    Supplier with the largest share of recommended order units.
    actions: only count decisions with these actions (e.g. ACTION_NOW); None = all.
    """
    if actions is not None:
        decisions = [d for d in decisions if d["action"] in actions]
    total = sum(d["order_quantity"] for d in decisions)
    if not decisions or total == 0:
        return None
    units: dict[str, int] = {}
    counts: dict[str, int] = {}
    for d in decisions:
        units[d["recommended_supplier"]] = units.get(d["recommended_supplier"], 0) + d["order_quantity"]
        counts[d["recommended_supplier"]] = counts.get(d["recommended_supplier"], 0) + 1
    supplier = max(units, key=units.get)
    return {"supplier": supplier, "units": units[supplier], "total_units": total,
            "share": units[supplier] / total, "decisions": counts[supplier],
            "total_decisions": len(decisions)}
