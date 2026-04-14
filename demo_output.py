"""
demo_output.py — clean, LinkedIn-friendly summary of the Supply Chain AI pipeline.
Runs all 3 agents silently and prints one decision line per SKU.
"""
from dotenv import load_dotenv
load_dotenv()

import sys
import logging
sys.stdout.reconfigure(encoding="utf-8")
logging.disable(logging.CRITICAL)   # silence all library loggers before crew runs

from src.crew import SupplyChainCrew
from src.schemas import ReorderReport

# -- Labels --------------------------------------------------------------------

_ACTION_LABEL = {
    "URGENT_REORDER": "URGENT REORDER",
    "REORDER":        "REORDER       ",
    "MONITOR":        "MONITOR       ",
}

_ACTION_BADGE = {
    "URGENT_REORDER": "!!!",
    "REORDER":        " ! ",
    "MONITOR":        " - ",
}

# -- Run -----------------------------------------------------------------------

print("Running Supply Chain AI pipeline... (this may take a minute)\n")

result = SupplyChainCrew(verbose=False).kickoff()
# CrewAI does not populate .pydantic on TaskOutput in this version —
# parse the JSON from .raw directly.
report = ReorderReport.model_validate_json(result.tasks_output[-1].raw)

# -- Format --------------------------------------------------------------------

WIDTH = 78
divider = "-" * WIDTH

counts = {"URGENT_REORDER": 0, "REORDER": 0, "MONITOR": 0}
for d in report.decisions:
    counts[d.action] += 1

print(divider)
print(f"  SUPPLY CHAIN AI — {len(report.decisions)} Reorder Decisions")
print(divider)
print()

for d in report.decisions:
    badge  = _ACTION_BADGE[d.action]
    label  = _ACTION_LABEL[d.action]
    reason = d.reason.rstrip(".")
    print(
        f"  [{badge}]  {d.sku:<6}  {d.product_type:<10}  "
        f"{label}  →  {d.recommended_supplier:<12}  qty: {d.order_quantity:>4}"
    )
    print(f"            {reason}.")
    print()

print(divider)
print(
    f"  URGENT REORDER: {counts['URGENT_REORDER']}   "
    f"REORDER: {counts['REORDER']}   "
    f"MONITOR: {counts['MONITOR']}"
)
print(divider)
