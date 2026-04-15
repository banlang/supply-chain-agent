import json
import re
import pandas as pd
from pathlib import Path
from crewai.tasks.task_output import TaskOutput

# Resolve path relative to this file: src/guardrails.py -> src/ -> project root
_DATA_PATH = str(Path(__file__).parent.parent / "data" / "supply_chain_data.csv")

VALID_RISK_LEVELS  = {"Critical", "High", "Medium"}
INVALID_RISK_LEVELS = {"Very High", "Severe", "Extreme", "Low Risk", "Moderate", "Critical Risk", "High Risk"}
VALID_SUPPLIERS    = {"Supplier 1", "Supplier 2", "Supplier 3", "Supplier 4", "Supplier 5"}
VALID_ACTIONS      = {"URGENT_REORDER", "REORDER", "MONITOR"}

# Load once at startup — used by all three guardrails
_supply_df          = pd.read_csv(_DATA_PATH)
_EXPECTED_SKU_COUNT = len(_supply_df)
_ZERO_STOCK_SKUS    = set(_supply_df.loc[_supply_df["Stock levels"] == 0, "SKU"])


def inventory_output_guardrail(output: TaskOutput) -> tuple[bool, str]:
    """
    Guardrail for inventory_analysis_task.
    Checks:
      1. Output contains real SKU codes (e.g. SKU0, SKU42)
      2. 'Total SKUs analyzed:' is present
      3. Reported count matches actual row count in the CSV
    """
    if not re.search(r'\bSKU\d+\b', output.raw):
        return (False, "No real SKU codes found. Call the Inventory Analysis Tool.")

    match = re.search(r'Total SKUs analyzed:\s*(\d+)', output.raw)
    if not match:
        return (False, "Missing 'Total SKUs analyzed:' — call the tool first.")

    reported = int(match.group(1))
    if reported != _EXPECTED_SKU_COUNT:
        return (
            False,
            f"Reported {reported} SKUs but dataset has {_EXPECTED_SKU_COUNT}. Do not generate data."
        )

    return (True, output)


def risk_output_guardrail(output: TaskOutput) -> tuple[bool, str]:
    """
    Guardrail for risk_detection_task.
    Blocks two failure modes:
      1. Invalid risk level names (LLM invented its own scale)
      2. No valid risk level found at all (agent skipped classification)
    """
    text = output.raw

    for invalid in INVALID_RISK_LEVELS:
        if invalid in text:
            return (
                False,
                f"Invalid risk level '{invalid}' found in output. "
                f"Use only: Critical, High, or Medium."
            )

    if not any(level in text for level in VALID_RISK_LEVELS):
        return (
            False,
            "No valid risk levels found. Classify every SKU as Critical, High, or Medium."
        )

    return (True, output)


def reorder_output_guardrail(output: TaskOutput) -> tuple[bool, str]:
    """
    Guardrail for reorder_recommendation_task. Validates every ReorderDecision:
      1. recommended_supplier must be one of Supplier 1–5
      2. action must be URGENT_REORDER, REORDER, or MONITOR
      3. any SKU with stock=0 in the dataset must have action=URGENT_REORDER
      4. order_quantity must be > 0
    Uses the Pydantic model when available; falls back to raw JSON parsing.
    """
    # Primary: Pydantic model is already parsed
    if output.pydantic and hasattr(output.pydantic, "decisions"):
        for d in output.pydantic.decisions:
            if d.recommended_supplier not in VALID_SUPPLIERS:
                return (
                    False,
                    f"Invalid supplier '{d.recommended_supplier}' for {d.sku}. "
                    f"Use only: {', '.join(sorted(VALID_SUPPLIERS))}.",
                )
            if d.action not in VALID_ACTIONS:
                return (
                    False,
                    f"Invalid action '{d.action}' for {d.sku}. "
                    f"Use only: URGENT_REORDER, REORDER, or MONITOR.",
                )
            if d.sku in _ZERO_STOCK_SKUS and d.action != "URGENT_REORDER":
                return (
                    False,
                    f"{d.sku} has stock=0 in the dataset — action must be URGENT_REORDER, "
                    f"not '{d.action}'. Read the Action line from the Supplier Comparison Tool output.",
                )
            if d.order_quantity <= 0:
                return (
                    False,
                    f"order_quantity for {d.sku} must be > 0, got {d.order_quantity}. "
                    f"Read the Order quantity line from the Supplier Comparison Tool output.",
                )
        return (True, output)

    # Fallback: parse raw JSON
    try:
        data = json.loads(output.raw)
        for d in data.get("decisions", []):
            sku      = d.get("sku", "")
            supplier = d.get("recommended_supplier", "")
            action   = d.get("action", "")
            qty      = d.get("order_quantity", 0)

            if supplier and supplier not in VALID_SUPPLIERS:
                return (
                    False,
                    f"Invalid supplier '{supplier}' for {sku}. "
                    f"Use only: {', '.join(sorted(VALID_SUPPLIERS))}.",
                )
            if action and action not in VALID_ACTIONS:
                return (
                    False,
                    f"Invalid action '{action}' for {sku}. "
                    f"Use only: URGENT_REORDER, REORDER, or MONITOR.",
                )
            if sku in _ZERO_STOCK_SKUS and action != "URGENT_REORDER":
                return (
                    False,
                    f"{sku} has stock=0 — action must be URGENT_REORDER, not '{action}'.",
                )
            if isinstance(qty, (int, float)) and qty <= 0:
                return (
                    False,
                    f"order_quantity for {sku} must be > 0, got {qty}.",
                )
    except (json.JSONDecodeError, AttributeError):
        # Last resort: at least one valid supplier must appear in the text
        if not any(s in output.raw for s in VALID_SUPPLIERS):
            return (
                False,
                f"No valid supplier found. Use only: {', '.join(sorted(VALID_SUPPLIERS))}.",
            )

    return (True, output)


def portfolio_output_guardrail(output: TaskOutput) -> tuple[bool, str]:
    """
    Guardrail for portfolio_synthesis_task.
    Checks:
      1. Output references at least 2 distinct SKU codes — confirms cross-SKU
         analysis rather than a single-SKU description.
    """
    sku_refs = set(re.findall(r'\bSKU\d+\b', output.raw))
    if len(sku_refs) < 2:
        return (
            False,
            "Portfolio synthesis must reference at least 2 distinct SKU codes. "
            "Analyse patterns ACROSS all decisions in your context — "
            "do not describe a single SKU in isolation.",
        )
    return (True, output)
