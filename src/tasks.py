import json
import re
import pandas as pd
from crewai import Task
from crewai.tasks.task_output import TaskOutput
from src.agents import data_analyst, risk_analyst, recommendation_agent
from src.tools import inventory_analysis_tool, risk_detection_tool, supplier_comparison_tool
from src.schemas import RiskDetectionOutput, ReorderReport

VALID_RISK_LEVELS = {"Critical", "High", "Medium"}
INVALID_RISK_LEVELS = {"Very High", "Severe", "Extreme", "Low Risk", "Moderate", "Critical Risk", "High Risk"}
VALID_SUPPLIERS = {"Supplier 1", "Supplier 2", "Supplier 3", "Supplier 4", "Supplier 5"}
VALID_ACTIONS   = {"URGENT_REORDER", "REORDER", "MONITOR"}

_supply_df = pd.read_csv("data/supply_chain_data.csv")
_EXPECTED_SKU_COUNT = len(_supply_df)
_ZERO_STOCK_SKUS = set(_supply_df.loc[_supply_df["Stock levels"] == 0, "SKU"])


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

inventory_analysis_task = Task(
    description=(
        "You MUST use the Inventory Analysis Tool to analyze the real dataset. "
        "Do NOT generate or assume any data. "
        "Call the tool with product_type='all' to get actual inventory data. "
        "Then based on the real tool output, identify SKUs at risk of stockout "
        "and prioritize by revenue density."
    ),
    expected_output=(
        "A structured inventory report based ONLY on real data from the tool:\n"
        "1. Summary: total SKUs analyzed, how many are at risk, how many are stockout\n"
        "2. Top 5 urgent SKUs with: SKU code, product type, stock level, "
        "days of stock remaining, revenue, and current supplier\n"
        "3. One clear recommendation sentence per urgent SKU"
    ),
    tools=[inventory_analysis_tool],
    agent=data_analyst,
    guardrail=inventory_output_guardrail
)

risk_detection_task = Task(
    description=(
        "The Data Analyst has identified at-risk SKUs. Their findings are available "
        "in your context.\n\n"
        "Extract the SKU codes from the inventory report, then call the Risk Detection "
        "Tool with those SKUs as a comma-separated string (e.g. 'SKU0,SKU5,SKU42'). "
        "Do NOT pass 'all' — only analyze the at-risk SKUs from the previous report. "
        "Do NOT invent risk scores or reasons. Use only what the tool returns."
    ),
    expected_output=(
        "A risk classification report for every at-risk SKU:\n"
        "1. Summary count: how many are Critical / High / Medium\n"
        "2. Per SKU: risk level, score, supplier, and the specific flags that drove the score "
        "(lead time, defect rate, inspection result)\n"
        "3. One action recommendation per Critical SKU"
    ),
    tools=[risk_detection_tool],
    agent=risk_analyst,
    context=[inventory_analysis_task],
    guardrail=risk_output_guardrail,
    output_pydantic=RiskDetectionOutput
)

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


reorder_recommendation_task = Task(
    description=(
        "You have the risk assessment from the Risk Analyst in your context.\n\n"
        "For EVERY SKU in the risk report, call the Supplier Comparison Tool once per SKU "
        "to get ranked supplier data. Do NOT skip any SKU. Do NOT call the tool with "
        "multiple SKUs at once — one call per SKU.\n\n"
        "When calling the tool, pass BOTH the sku AND the risk_level from the risk report "
        "(e.g. sku='SKU3', risk_level='High').\n\n"
        "The tool returns a RECOMMENDATION block with pre-calculated values — use them exactly:\n"
        "  - action             : read from 'Action' line in the tool output\n"
        "  - recommended_supplier: read from 'Recommended' line in the tool output\n"
        "  - order_quantity     : read the integer from 'Order quantity' line in the tool output\n"
        "  - rejected_suppliers : read from 'Rejected suppliers' line in the tool output\n"
        "  - reason: one sentence combining risk level, why this supplier, and why this quantity\n\n"
        "Do NOT invent supplier names, scores, or quantities. Use only what the tool returns."
    ),
    expected_output=(
        "A reorder decision for every SKU from the risk report:\n"
        "Per SKU: action, recommended supplier, order quantity, reason, rejected suppliers"
    ),
    tools=[supplier_comparison_tool],
    agent=recommendation_agent,
    context=[risk_detection_task],
    guardrail=reorder_output_guardrail,
    output_pydantic=ReorderReport
)