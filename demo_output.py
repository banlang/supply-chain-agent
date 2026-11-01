"""
demo_output.py — clean, LinkedIn-friendly summary of the Supply Chain AI pipeline.
Runs all 4 agents silently and prints one decision line per SKU plus portfolio insights.
Also saves results to demo_output.json for the Streamlit dashboard and appends
one line per run (success or failure) to logs/run_log.jsonl.
"""
from dotenv import load_dotenv
load_dotenv()

import json
import sys
import logging
import uuid
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")
logging.disable(logging.CRITICAL)   # silence all library loggers before crew runs

import crewai
from src.crew import SupplyChainCrew
from src.schemas import ReorderReport, PortfolioInsight
from src.run_log import append_run, build_run_record, load_pricing

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

started_at = datetime.now().astimezone()
run_id     = f"run-{started_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
crew       = SupplyChainCrew(verbose=False)


def _log_run(status: str, usage: dict, n_decisions: int, error: str | None = None) -> None:
    append_run(build_run_record(
        run_id=run_id, started_at=started_at, ended_at=datetime.now().astimezone(),
        status=status, tasks=crew.tasks, model=crew.model,
        crewai_version=crewai.__version__, guardrails=crew.guardrail_recorder.summary(),
        usage=usage, pricing=load_pricing(), n_decisions=n_decisions, error=error,
    ))


try:
    result = crew.kickoff()
    # CrewAI does not populate .pydantic on TaskOutput in this version —
    # parse the JSON from .raw directly.
    # tasks_output[-2] = reorder_recommendation_task
    # tasks_output[-1] = portfolio_synthesis_task
    report  = ReorderReport.model_validate_json(result.tasks_output[-2].raw)
    insight = PortfolioInsight.model_validate_json(result.tasks_output[-1].raw)
except Exception as exc:
    _log_run("failed", usage={}, n_decisions=0, error=f"{type(exc).__name__}: {exc}"[:500])
    raise

_log_run("success", usage=result.token_usage.model_dump(), n_decisions=len(report.decisions))

# -- Cache to JSON for Streamlit dashboard ------------------------------------
with open("demo_output.json", "w", encoding="utf-8") as f:
    json.dump({
        "run_id": run_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "decisions": [d.model_dump() for d in report.decisions],
        "patterns": insight.patterns,
        "concentration_risks": insight.concentration_risks,
        "executive_summary": insight.executive_summary,
    }, f, indent=2, ensure_ascii=False)
print(f"Saved demo_output.json  (run {run_id}, logged to logs/run_log.jsonl)\n")

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

# -- Portfolio Insight (Agent 4) -----------------------------------------------

print()
print(divider)
print(f"  PORTFOLIO INSIGHTS  (Agent 4)")
print(divider)

print()
print("  ### 1. Executive Summary")
print(f"  {insight.executive_summary}")

print()
print("  ### 2. Critical Warnings")
if insight.concentration_risks:
    for risk in insight.concentration_risks:
        print(f"  ! {risk}")
else:
    print("  (none)")

print()
print("  ### 3. Key Insights")
for pattern in insight.patterns:
    print(f"  - {pattern}")
print()
print(divider)
