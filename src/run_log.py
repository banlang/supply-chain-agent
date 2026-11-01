"""
Pipeline run logging for logs/run_log.jsonl (written by demo_output.py).

Records only what a real run produced: timings from CrewAI tasks, guardrail
calls captured by GuardrailRecorder, token usage from CrewAI usage metrics,
and a cost ESTIMATE from dated price constants in config/pricing.yaml.
"""
import json
from datetime import datetime
from pathlib import Path

import yaml

_ROOT        = Path(__file__).parent.parent
RUN_LOG      = _ROOT / "logs" / "run_log.jsonl"
PRICING_PATH = _ROOT / "config" / "pricing.yaml"


def load_pricing(path: Path = PRICING_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def estimate_cost(usage: dict, model: str, pricing: dict) -> float | None:
    """USD estimate; cached prompt tokens billed at the cached rate. None if model unpriced."""
    prices = pricing.get("per_million_tokens", {}).get(model)
    if prices is None:
        return None
    prompt    = usage.get("prompt_tokens", 0)
    cached    = usage.get("cached_prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return (
        (prompt - cached) * prices["input"]
        + cached * prices.get("cached_input", prices["input"])
        + completion * prices["output"]
    ) / 1_000_000


class GuardrailRecorder:
    """Wraps guardrail functions and records every call's pass/fail per task."""

    def __init__(self):
        self.calls: dict[str, list[bool]] = {}

    def wrap(self, task_name: str, guardrail):
        # One required parameter — CrewAI validates the guardrail signature.
        def recorded(output):
            ok, result = guardrail(output)
            self.calls.setdefault(task_name, []).append(bool(ok))
            return ok, result
        return recorded

    def summary(self) -> dict[str, dict]:
        """retries = calls after the first (each failed call triggers one retry)."""
        return {
            task: {"calls": len(c), "passed": sum(c), "failed": len(c) - sum(c),
                   "retries": len(c) - 1, "first_pass": c[0]}
            for task, c in self.calls.items()
        }


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def build_run_record(
    *,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    status: str,
    tasks: list,
    model: str,
    crewai_version: str,
    guardrails: dict,
    usage: dict,
    pricing: dict,
    n_decisions: int,
    error: str | None = None,
) -> dict:
    """tasks: CrewAI Task objects (name, start_time, end_time)."""
    cost = estimate_cost(usage, model, pricing)
    return {
        "run_id":         run_id,
        "start":          _iso(started_at),
        "end":            _iso(ended_at),
        "duration_s":     round((ended_at - started_at).total_seconds(), 1),
        "status":         status,
        "error":          error,
        "model":          model,
        "crewai_version": crewai_version,
        "tasks": [
            {
                "task":       t.name,
                "start":      _iso(t.start_time),
                "end":        _iso(t.end_time),
                "duration_s": (round((t.end_time - t.start_time).total_seconds(), 1)
                               if t.start_time and t.end_time else None),
                "guardrail":  guardrails.get(t.name),
            }
            for t in tasks
        ],
        "token_usage": {k: usage.get(k, 0) for k in
                        ("prompt_tokens", "cached_prompt_tokens", "completion_tokens",
                         "total_tokens", "successful_requests")},
        "n_decisions":           n_decisions,
        "estimated_cost_usd":    cost,
        "cost_per_decision_usd": cost / n_decisions if cost is not None and n_decisions else None,
        "prices_as_of":          pricing.get("prices_as_of"),
        "price_source":          pricing.get("source"),
    }


def append_run(record: dict, path: Path = RUN_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
