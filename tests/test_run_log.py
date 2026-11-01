"""
Tests for src/run_log.py — cost estimate, guardrail recording, run record,
and wiring of the recorder into the real crew (no LLM call is made).
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.run_log import (
    GuardrailRecorder, append_run, build_run_record, estimate_cost, load_pricing,
)

PRICING = load_pricing()
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def test_pricing_file_is_dated_and_sourced():
    assert PRICING["prices_as_of"] == "2026-11-01"
    assert PRICING["source"].startswith("https://")
    assert PRICING["per_million_tokens"]["gpt-4o-mini"] == {"input": 0.15, "cached_input": 0.075, "output": 0.60}


class TestEstimateCost:
    def test_input_and_output(self):
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
        assert estimate_cost(usage, "gpt-4o-mini", PRICING) == pytest.approx(0.75)

    def test_cached_tokens_billed_at_cached_rate(self):
        usage = {"prompt_tokens": 1_000_000, "cached_prompt_tokens": 400_000, "completion_tokens": 0}
        assert estimate_cost(usage, "gpt-4o-mini", PRICING) == pytest.approx(0.6 * 0.15 + 0.4 * 0.075)

    def test_unpriced_model_is_none(self):
        assert estimate_cost({"prompt_tokens": 10}, "some-other-model", PRICING) is None


class TestGuardrailRecorder:
    def test_records_pass_fail_and_retries(self):
        rec = GuardrailRecorder()
        answers = iter([(False, "fix"), (False, "fix"), (True, "ok")])
        g = rec.wrap("portfolio_synthesis_task", lambda out: next(answers))
        assert [g(None)[0] for _ in range(3)] == [False, False, True]
        rec.wrap("inventory_analysis_task", lambda out: (True, out))("x")
        s = rec.summary()
        assert s["portfolio_synthesis_task"] == {"calls": 3, "passed": 1, "failed": 2, "retries": 2, "first_pass": False}
        assert s["inventory_analysis_task"] == {"calls": 1, "passed": 1, "failed": 0, "retries": 0, "first_pass": True}

    def test_wrapper_passes_result_through(self):
        g = GuardrailRecorder().wrap("t", lambda out: (False, "specific correction"))
        assert g("output") == (False, "specific correction")

    def test_crew_tasks_use_recorded_guardrails(self):
        # Builds the real crew (CrewAI validates guardrail signatures) — no LLM call
        from src.crew import SupplyChainCrew
        crew = SupplyChainCrew(verbose=False)
        assert [t.name for t in crew.tasks] == [
            "inventory_analysis_task", "risk_detection_task",
            "reorder_recommendation_task", "portfolio_synthesis_task",
        ]
        ok, _ = crew.risk_detection_task.guardrail(SimpleNamespace(raw="SKU2: High", pydantic=None))
        assert ok is True
        assert crew.guardrail_recorder.summary()["risk_detection_task"]["calls"] == 1
        assert crew.model == "gpt-4o-mini"


class TestRunRecord:
    def _record(self, **kw):
        tasks = [SimpleNamespace(name="inventory_analysis_task", start_time=T0, end_time=T0 + timedelta(seconds=12)),
                 SimpleNamespace(name="portfolio_synthesis_task", start_time=None, end_time=None)]
        args = dict(
            run_id="run-x", started_at=T0, ended_at=T0 + timedelta(seconds=90), status="success",
            tasks=tasks, model="gpt-4o-mini", crewai_version="1.14.4",
            guardrails={"inventory_analysis_task": {"calls": 1, "passed": 1, "failed": 0, "retries": 0, "first_pass": True}},
            usage={"prompt_tokens": 20_000, "completion_tokens": 2_000, "total_tokens": 22_000, "successful_requests": 9},
            pricing=PRICING, n_decisions=5,
        )
        args.update(kw)
        return build_run_record(**args)

    def test_fields(self):
        r = self._record()
        assert r["duration_s"] == 90.0
        assert r["tasks"][0]["duration_s"] == 12.0 and r["tasks"][0]["guardrail"]["calls"] == 1
        assert r["tasks"][1]["duration_s"] is None and r["tasks"][1]["guardrail"] is None
        assert r["token_usage"]["total_tokens"] == 22_000
        assert r["estimated_cost_usd"] == pytest.approx((20_000 * 0.15 + 2_000 * 0.60) / 1e6)
        assert r["cost_per_decision_usd"] == pytest.approx(r["estimated_cost_usd"] / 5)
        assert r["prices_as_of"] == "2026-11-01"

    def test_failed_run_has_no_cost_per_decision(self):
        r = self._record(status="failed", usage={}, n_decisions=0, error="boom")
        assert r["status"] == "failed" and r["error"] == "boom"
        assert r["cost_per_decision_usd"] is None

    def test_append_run(self, tmp_path):
        path = tmp_path / "logs" / "run_log.jsonl"
        append_run(self._record(), path)
        append_run(self._record(run_id="run-y"), path)
        assert [json.loads(l)["run_id"] for l in path.read_text(encoding="utf-8").splitlines()] == ["run-x", "run-y"]
