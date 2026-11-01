"""
Tests for src/review.py — SLA status, decision validation, append-only log,
review queue (recompute versions, defer), override/edit rates, autonomy.

All log files are written to pytest's tmp_path — never to logs/.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.review import (
    append_entry, autonomy_status, build_queue, decision_outcomes, decision_stats, format_duration,
    load_policy, make_entry, next_version, overdue_counts, read_jsonl,
    recommendation_id, reject_path_breakdown, sla_status, time_to_decision_hours,
)

POLICY = load_policy()
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
RUN = "run-test"

DECISIONS = [
    {"sku": "SKU16", "product_type": "skincare", "action": "MONITOR", "recommended_supplier": "Supplier 1", "order_quantity": 186},
    {"sku": "SKU2",  "product_type": "haircare", "action": "REORDER", "recommended_supplier": "Supplier 3", "order_quantity": 9},
    {"sku": "SKU68", "product_type": "haircare", "action": "URGENT_REORDER", "recommended_supplier": "Supplier 4", "order_quantity": 119},
    {"sku": "SKU24", "product_type": "haircare", "action": "REORDER", "recommended_supplier": "Supplier 3", "order_quantity": 224},
]
REVENUE = {"SKU16": 100.0, "SKU2": 500.0, "SKU68": 50.0, "SKU24": 900.0}


def _queue(entries=(), today=date(2026, 11, 1)):
    return build_queue(DECISIONS, RUN, T0.isoformat(), list(entries), REVENUE, today)


def _rec(sku="SKU2"):
    pending, _ = _queue()
    return next(r for r in pending if r["sku"] == sku)


def _entry(sku="SKU2", final_action="approved", minutes=60, **kw):
    return make_entry(rec=_rec(sku), reviewer="Alex", final_action=final_action, policy=POLICY,
                      now=T0 + timedelta(minutes=minutes), **kw)


# ---------------------------------------------------------------------------
# Policy + SLA
# ---------------------------------------------------------------------------

def test_policy_is_labelled_as_assumption():
    assert POLICY["label"] == "policy assumption"
    assert POLICY["sla"]["URGENT_REORDER"] == {"hours": 24}
    assert "URGENT_REORDER" in POLICY["no_defer_tiers"]


class TestSlaStatus:
    def test_within_sla(self):
        s = sla_status("URGENT_REORDER", T0, T0 + timedelta(hours=20), POLICY)
        assert s["overdue"] is False and s["escalate"] is False
        assert s["label"] == "Within SLA — 4h 0m left"

    def test_overdue_urgent_escalates(self):
        s = sla_status("URGENT_REORDER", T0, T0 + timedelta(hours=30), POLICY)
        assert s["overdue"] is True and s["escalate"] is True
        assert s["label"] == "OVERDUE by 6h 0m"

    def test_overdue_reorder_does_not_escalate(self):
        s = sla_status("REORDER", T0, T0 + timedelta(days=8), POLICY)
        assert s["overdue"] is True and s["escalate"] is False

    def test_decided_within_and_after_sla(self):
        assert sla_status("MONITOR", T0, T0, POLICY, decided_at=T0 + timedelta(days=13))["overdue"] is False
        late = sla_status("MONITOR", T0, T0, POLICY, decided_at=T0 + timedelta(days=15))
        assert late["overdue"] is True and late["label"] == "Decided 1d 0h after SLA"

    def test_unknown_recommendation_time(self):
        s = sla_status("REORDER", None, T0, POLICY)
        assert s["known"] is False and s["overdue"] is None

    def test_format_duration(self):
        assert format_duration(timedelta(days=2, hours=3)) == "2d 3h"
        assert format_duration(timedelta(minutes=7)) == "7m"


# ---------------------------------------------------------------------------
# make_entry validation
# ---------------------------------------------------------------------------

class TestMakeEntry:
    def test_approve_copies_ai_values(self):
        e = _entry()
        assert (e["final_action"], e["final_qty"], e["final_supplier"]) == ("approved", 9, "Supplier 3")
        assert (e["ai_action"], e["ai_supplier"], e["ai_qty"]) == ("REORDER", "Supplier 3", 9)
        assert e["recommendation_id"] == recommendation_id(RUN, "SKU2", 1) == "run-test:SKU2:v1"
        assert e["reject_path"] is None and e["reason"] == ""

    def test_has_all_required_fields(self):
        required = {"run_id", "timestamp", "reviewer", "sku", "ai_action", "ai_supplier", "ai_qty",
                    "final_action", "final_qty", "reason", "reject_path", "linked_recommendation_id"}
        assert required <= set(_entry())

    def test_reviewer_required(self):
        with pytest.raises(ValueError, match="reviewer"):
            make_entry(rec=_rec(), reviewer="  ", final_action="approved", policy=POLICY)

    @pytest.mark.parametrize("final_action,kw", [
        ("edited", {"final_qty": 20}),
        ("rejected", {"reject_path": "manual_order", "final_supplier": "Supplier 1", "final_qty": 5}),
    ])
    def test_reason_required_for_edit_and_reject(self, final_action, kw):
        with pytest.raises(ValueError, match="reason is required"):
            _entry(final_action=final_action, reason="  ", **kw)

    def test_edit(self):
        e = _entry(final_action="edited", final_qty=20, reason="MOQ is 20")
        assert (e["final_qty"], e["final_supplier"], e["reason"]) == (20, "Supplier 3", "MOQ is 20")

    def test_edit_same_qty_rejected(self):
        with pytest.raises(ValueError, match="use Approve"):
            _entry(final_action="edited", final_qty=9, reason="x")

    def test_reject_requires_next_step(self):
        with pytest.raises(ValueError, match="next step"):
            _entry(final_action="rejected", reason="wrong supplier")

    def test_reject_manual_order_logged_as_human_decision(self):
        e = _entry(final_action="rejected", reason="contract", reject_path="manual_order",
                   final_supplier="Supplier 1", final_qty=12)
        assert (e["reject_path"], e["final_supplier"], e["final_qty"]) == ("manual_order", "Supplier 1", 12)

    def test_manual_order_needs_supplier_and_qty(self):
        with pytest.raises(ValueError, match="supplier"):
            _entry(final_action="rejected", reason="x", reject_path="manual_order", final_qty=5)
        with pytest.raises(ValueError, match="quantity"):
            _entry(final_action="rejected", reason="x", reject_path="manual_order", final_supplier="Supplier 1")

    def test_defer_not_allowed_for_urgent(self):
        with pytest.raises(ValueError, match="not allowed for URGENT_REORDER"):
            _entry(sku="SKU68", final_action="rejected", reason="x", reject_path="defer",
                   recheck_date=date(2026, 11, 8))

    def test_defer_needs_date(self):
        with pytest.raises(ValueError, match="re-check date"):
            _entry(final_action="rejected", reason="x", reject_path="defer")

    def test_defer_logged_without_order(self):
        e = _entry(final_action="rejected", reason="promo ends", reject_path="defer", recheck_date=date(2026, 11, 8))
        assert (e["recheck_date"], e["final_qty"], e["final_supplier"]) == ("2026-11-08", None, None)

    def test_recompute_needs_result(self):
        with pytest.raises(ValueError, match="Recompute"):
            _entry(final_action="rejected", reason="x", reject_path="recompute")


# ---------------------------------------------------------------------------
# Log writing
# ---------------------------------------------------------------------------

class TestLogFile:
    def test_append_only_jsonl(self, tmp_path):
        path = tmp_path / "logs" / "decisions_log.jsonl"
        append_entry(_entry(), path)
        append_entry(_entry(sku="SKU24"), path)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert [json.loads(l)["sku"] for l in lines] == ["SKU2", "SKU24"]

    def test_read_missing_file(self, tmp_path):
        assert read_jsonl(tmp_path / "nope.jsonl") == ([], 0)

    def test_read_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "x.jsonl"
        path.write_text('{"a": 1}\nnot json\n\n{"a": 2}\n', encoding="utf-8")
        records, bad = read_jsonl(path)
        assert records == [{"a": 1}, {"a": 2}] and bad == 1


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

class TestQueue:
    def test_sorted_by_tier_then_revenue(self):
        pending, deferred = _queue()
        assert [r["sku"] for r in pending] == ["SKU68", "SKU24", "SKU2", "SKU16"]
        assert deferred == []

    def test_decided_items_leave_queue(self):
        pending, _ = _queue([_entry()])
        assert "SKU2" not in [r["sku"] for r in pending]

    def test_other_runs_ignored(self):
        e = {**_entry(), "run_id": "older-run"}
        pending, _ = _queue([e])
        assert "SKU2" in [r["sku"] for r in pending]

    def test_recompute_adds_linked_version(self):
        recomputed = {"recommendation_id": "run-test:SKU2:v2", "version": 2, "action": "REORDER",
                      "supplier": "Supplier 5", "qty": 10, "excluded": ["Supplier 3"]}
        e = _entry(final_action="rejected", reason="Supplier 3 on hold", reject_path="recompute",
                   recomputed=recomputed)
        pending, _ = _queue([e])
        v2 = next(r for r in pending if r["sku"] == "SKU2")
        assert v2["recommendation_id"] == "run-test:SKU2:v2"
        assert v2["linked_recommendation_id"] == "run-test:SKU2:v1"
        assert (v2["supplier"], v2["qty"], v2["version"]) == ("Supplier 5", 10, 2)
        assert v2["source"] == "Deterministic recompute (no LLM)"
        assert next_version(v2, [e]) == 3

    def test_deferred_returns_on_recheck_date(self):
        e = _entry(final_action="rejected", reason="x", reject_path="defer", recheck_date=date(2026, 11, 8))
        pending, deferred = _queue([e], today=date(2026, 11, 7))
        assert "SKU2" not in [r["sku"] for r in pending] and deferred[0]["sku"] == "SKU2"
        pending, deferred = _queue([e], today=date(2026, 11, 8))
        assert "SKU2" in [r["sku"] for r in pending] and deferred == []


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _mixed_entries():
    return [
        _entry("SKU2", "approved", minutes=60),
        _entry("SKU24", "edited", minutes=120, final_qty=230, reason="pack size"),
        _entry("SKU68", "rejected", minutes=30, reason="contract", reject_path="manual_order",
               final_supplier="Supplier 1", final_qty=119),
        _entry("SKU16", "approved", minutes=60 * 24 * 15),
    ]


class TestStats:
    def test_overall_rates(self):
        o = decision_stats(_mixed_entries())["overall"]
        assert (o["n"], o["approved"], o["edited"], o["rejected"]) == (4, 2, 1, 1)
        assert o["override_rate"] == 0.5   # (1 edited + 1 rejected) / 4
        assert o["edit_rate"] == 0.25

    def test_rates_by_tier(self):
        t = decision_stats(_mixed_entries())["by_tier"]
        assert t["REORDER"]["n"] == 2 and t["REORDER"]["edit_rate"] == 0.5
        assert t["URGENT_REORDER"]["override_rate"] == 1.0
        assert t["MONITOR"]["override_rate"] == 0.0

    def test_no_decisions_means_not_measured(self):
        o = decision_stats([])["overall"]
        assert o["n"] == 0 and o["override_rate"] is None and o["edit_rate"] is None

    def test_time_to_decision(self):
        ttd = time_to_decision_hours(_mixed_entries())
        assert ttd["URGENT_REORDER"] == 0.5
        assert ttd["REORDER"] == 1.5      # median of 1h and 2h
        assert ttd["MONITOR"] == 360

    def test_decision_outcomes_sum_to_total(self):
        assert decision_outcomes(DECISIONS, RUN, []) == {"approved": 0, "overridden": 0, "pending": 4}
        # SKU2 approved, SKU24 edited, SKU68 rejected, SKU16 approved
        assert decision_outcomes(DECISIONS, RUN, _mixed_entries()) == {"approved": 2, "overridden": 2, "pending": 0}
        recomputed = {"recommendation_id": "run-test:SKU2:v2", "version": 2, "action": "REORDER",
                      "supplier": "Supplier 5", "qty": 10}
        entries = [
            _entry("SKU2", "rejected", reason="x", reject_path="recompute", recomputed=recomputed),
            _entry("SKU16", "rejected", reason="x", reject_path="defer", recheck_date=date(2026, 11, 8)),
            {**_entry("SKU24"), "run_id": "older-run"},
        ]
        # v2 left pending in the queue does not add a sixth count; other runs are ignored
        o = decision_outcomes(DECISIONS, RUN, entries)
        assert o == {"approved": 0, "overridden": 2, "pending": 2}
        assert sum(o.values()) == len(DECISIONS)

    def test_reject_path_breakdown(self):
        assert reject_path_breakdown(_mixed_entries()) == {"manual_order": 1, "recompute": 0, "defer": 0}

    def test_overdue_counts(self):
        entries = _mixed_entries()   # SKU16 MONITOR decided after 15 days > 14-day SLA
        pending, _ = _queue()
        ov = overdue_counts(entries, pending, T0 + timedelta(hours=25), POLICY)
        assert ov["decided_late"] == 1
        assert ov["pending_overdue"] == 1 and ov["escalations"] == 1   # SKU68 URGENT > 24h

    def test_autonomy_needs_enough_decisions(self):
        status = autonomy_status(decision_stats(_mixed_entries()), POLICY)
        assert status["MONITOR"].startswith("Not enough evidence — 1 of 50")

    def test_autonomy_candidate_and_keep_review(self):
        approve = [_entry("SKU16", "approved")] * 50
        assert autonomy_status(decision_stats(approve), POLICY)["MONITOR"].startswith("Candidate")
        edits = approve[:49] + [_entry("SKU16", "edited", final_qty=1, reason="x")]
        assert autonomy_status(decision_stats(edits), POLICY)["MONITOR"].startswith("Keep full review")
