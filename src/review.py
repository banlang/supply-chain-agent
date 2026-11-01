"""
Human review of AI reorder recommendations: decision log, SLA status,
review queue and override/edit statistics.

Pure functions except append_entry() / read_jsonl(). Nothing here invents
data — every statistic is computed from logs/decisions_log.jsonl, which only
grows when a real reviewer acts in the Streamlit UI.
"""
import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src.analytics import ACTION_ORDER

_ROOT         = Path(__file__).parent.parent
LOG_DIR       = _ROOT / "logs"
DECISIONS_LOG = LOG_DIR / "decisions_log.jsonl"
POLICY_PATH   = _ROOT / "config" / "policy.yaml"

FINAL_ACTIONS = ("approved", "edited", "rejected")
REJECT_PATHS  = ("manual_order", "recompute", "defer")


def load_policy(path: Path = POLICY_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def format_duration(td: timedelta) -> str:
    """'2d 3h', '5h 12m', '7m' — absolute value."""
    minutes = int(abs(td).total_seconds() // 60)
    days, rem = divmod(minutes, 60 * 24)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# ---------------------------------------------------------------------------
# SLA
# ---------------------------------------------------------------------------

def sla_timedelta(action: str, policy: dict) -> timedelta:
    return timedelta(**policy["sla"][action])


def sla_status(
    action: str,
    recommended_at: datetime | None,
    now: datetime,
    policy: dict,
    decided_at: datetime | None = None,
) -> dict:
    """
    SLA = time from recommendation to human decision (config/policy.yaml).
    Pending URGENT_REORDER items past their SLA are flagged for escalation
    (display only — nothing is sent and nothing is auto-approved).
    """
    if recommended_at is None:
        return {"known": False, "overdue": None, "escalate": False,
                "label": "SLA unknown — recommendation time not recorded"}

    deadline = recommended_at + sla_timedelta(action, policy)
    if decided_at is not None:
        overdue = decided_at > deadline
        label = (f"Decided {format_duration(decided_at - deadline)} after SLA" if overdue
                 else "Decided within SLA")
        return {"known": True, "overdue": overdue, "escalate": False, "label": label,
                "deadline": deadline}

    overdue = now > deadline
    label = (f"OVERDUE by {format_duration(now - deadline)}" if overdue
             else f"Within SLA — {format_duration(deadline - now)} left")
    return {"known": True, "overdue": overdue,
            "escalate": overdue and action == "URGENT_REORDER",
            "label": label, "deadline": deadline}


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------

def recommendation_id(run_id: str, sku: str, version: int = 1) -> str:
    return f"{run_id}:{sku}:v{version}"


def make_entry(
    *,
    rec: dict,
    reviewer: str,
    final_action: str,
    policy: dict,
    final_qty: int | None = None,
    final_supplier: str | None = None,
    reason: str = "",
    reject_path: str | None = None,
    recheck_date: date | None = None,
    recomputed: dict | None = None,
    batch: bool = False,
    now: datetime | None = None,
) -> dict:
    """
    Validate a reviewer action and build one decisions_log line.
    Raises ValueError with a user-facing message if the action is incomplete.

    rec: queue item — run_id, recommendation_id, linked_recommendation_id,
         sku, action, supplier, qty, recommended_at.
    """
    reviewer = (reviewer or "").strip()
    reason   = (reason or "").strip()
    if not reviewer:
        raise ValueError("Enter your name as reviewer before recording a decision.")
    if final_action not in FINAL_ACTIONS:
        raise ValueError(f"Unknown action '{final_action}'.")
    if final_action in ("edited", "rejected") and not reason:
        raise ValueError("A reason is required to edit or reject a recommendation.")

    if final_action == "approved":
        final_qty, final_supplier, reject_path = rec["qty"], rec["supplier"], None
    elif final_action == "edited":
        if not final_qty or final_qty <= 0:
            raise ValueError("Edited quantity must be greater than 0.")
        if final_qty == rec["qty"]:
            raise ValueError("Edited quantity is the same as the AI quantity — use Approve.")
        final_supplier, reject_path = rec["supplier"], None
    else:  # rejected
        if reject_path not in REJECT_PATHS:
            raise ValueError("Choose a next step for the rejection: manual order, recompute, or defer.")
        if reject_path == "manual_order":
            if not final_supplier:
                raise ValueError("Manual order needs a supplier.")
            if not final_qty or final_qty <= 0:
                raise ValueError("Manual order quantity must be greater than 0.")
        else:
            final_qty, final_supplier = None, None
        if reject_path == "defer":
            if rec["action"] in policy.get("no_defer_tiers", []):
                raise ValueError(f"Defer is not allowed for {rec['action']} — order manually or recompute.")
            if recheck_date is None:
                raise ValueError("Defer needs a re-check date.")
        if reject_path == "recompute" and not recomputed:
            raise ValueError("Recompute produced no recommendation.")

    return {
        "run_id":                   rec["run_id"],
        "timestamp":                (now or now_local()).isoformat(timespec="seconds"),
        "reviewer":                 reviewer,
        "recommendation_id":        rec["recommendation_id"],
        "linked_recommendation_id": rec.get("linked_recommendation_id"),
        "recommended_at":           rec.get("recommended_at"),
        "sku":                      rec["sku"],
        "ai_action":                rec["action"],
        "ai_supplier":              rec["supplier"],
        "ai_qty":                   rec["qty"],
        "final_action":             final_action,
        "final_supplier":           final_supplier,
        "final_qty":                final_qty,
        "reason":                   reason,
        "reject_path":              reject_path,
        "recheck_date":             recheck_date.isoformat() if recheck_date else None,
        "recomputed":               recomputed,
        "batch":                    batch,
    }


def append_entry(entry: dict, path: Path = DECISIONS_LOG) -> None:
    """Append-only: one JSON object per line, never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    """Return (records, malformed_line_count). Missing file → ([], 0)."""
    if not path.exists():
        return [], 0
    records, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return records, bad


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def build_queue(
    decisions: list[dict],
    run_id: str,
    recommended_at: str | None,
    entries: list[dict],
    revenue_by_sku: dict[str, float],
    today: date,
) -> tuple[list[dict], list[dict]]:
    """
    Return (pending, deferred) recommendations for this run.

    - One v1 recommendation per AI decision.
    - Each 'recompute' rejection adds a new version linked to the rejected one.
    - A recommendation leaves the queue once it has a logged decision, except
      'defer', which returns when its re-check date is reached.
    Sorted URGENT_REORDER → REORDER → MONITOR, then revenue at risk (desc).
    """
    recs = [{
        "run_id": run_id,
        "recommendation_id": recommendation_id(run_id, d["sku"], 1),
        "linked_recommendation_id": None,
        "version": 1,
        "source": "AI pipeline",
        "recommended_at": recommended_at,
        "sku": d["sku"],
        "product_type": d.get("product_type", ""),
        "action": d["action"],
        "supplier": d["recommended_supplier"],
        "qty": d["order_quantity"],
        "rejected_suppliers": d.get("rejected_suppliers", []),
        "reason_text": d.get("reason", ""),
        "excluded": [],
    } for d in decisions]

    run_entries = [e for e in entries if e.get("run_id") == run_id]
    for e in run_entries:
        r = e.get("recomputed")
        if e.get("reject_path") == "recompute" and r:
            recs.append({
                "run_id": run_id,
                "recommendation_id": r["recommendation_id"],
                "linked_recommendation_id": e["recommendation_id"],
                "version": r["version"],
                "source": "Deterministic recompute (no LLM)",
                "recommended_at": e["timestamp"],
                "sku": e["sku"],
                "product_type": r.get("product_type", ""),
                "action": r["action"],
                "supplier": r["supplier"],
                "qty": r["qty"],
                "rejected_suppliers": r.get("rejected_suppliers", []),
                "reason_text": "",
                "excluded": r.get("excluded", []),
            })

    latest = {}
    for e in run_entries:
        latest[e["recommendation_id"]] = e

    pending, deferred = [], []
    for rec in recs:
        e = latest.get(rec["recommendation_id"])
        if e is None:
            pending.append(rec)
        elif e.get("reject_path") == "defer":
            due = date.fromisoformat(e["recheck_date"])
            rec = {**rec, "recheck_date": e["recheck_date"]}
            (pending if today >= due else deferred).append(rec)

    def sort_key(r):
        return (ACTION_ORDER.index(r["action"]), -revenue_by_sku.get(r["sku"], 0.0))

    return sorted(pending, key=sort_key), sorted(deferred, key=sort_key)


def next_version(rec: dict, entries: list[dict]) -> int:
    """Next version number for a SKU in this run (v1 is the AI recommendation)."""
    versions = [1] + [
        e["recomputed"]["version"] for e in entries
        if e.get("run_id") == rec["run_id"] and e.get("sku") == rec["sku"] and e.get("recomputed")
    ]
    return max(versions) + 1


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _rates(rows: list[dict]) -> dict:
    n = len(rows)
    approved = sum(1 for e in rows if e["final_action"] == "approved")
    edited   = sum(1 for e in rows if e["final_action"] == "edited")
    rejected = sum(1 for e in rows if e["final_action"] == "rejected")
    return {
        "n": n, "approved": approved, "edited": edited, "rejected": rejected,
        "approval_rate": approved / n if n else None,
        # override = the human changed the outcome (edit or reject)
        "override_rate": (edited + rejected) / n if n else None,
        "edit_rate":     edited / n if n else None,
    }


def decision_stats(entries: list[dict]) -> dict:
    """Overall and per-tier approval / override / edit rates."""
    return {
        "overall": _rates(entries),
        "by_tier": {a: _rates([e for e in entries if e["ai_action"] == a]) for a in ACTION_ORDER},
    }


def decision_outcomes(decisions: list[dict], run_id: str, entries: list[dict]) -> dict[str, int]:
    """
    One outcome per AI decision, from the latest logged action on its v1 recommendation:
    approved; overridden = edited or rejected (any next step); pending = no action yet.
    The three counts always sum to len(decisions).
    """
    latest = {}
    for e in entries:
        if e.get("run_id") == run_id:
            latest[e["recommendation_id"]] = e
    out = {"approved": 0, "overridden": 0, "pending": 0}
    for d in decisions:
        e = latest.get(recommendation_id(run_id, d["sku"], 1))
        if e is None:
            out["pending"] += 1
        elif e["final_action"] == "approved":
            out["approved"] += 1
        else:
            out["overridden"] += 1
    return out


def time_to_decision_hours(entries: list[dict]) -> dict[str, float | None]:
    """Median hours from recommendation to decision, per tier."""
    out = {}
    for a in ACTION_ORDER:
        hours = [
            (parse_ts(e["timestamp"]) - parse_ts(e["recommended_at"])).total_seconds() / 3600
            for e in entries if e["ai_action"] == a and e.get("recommended_at")
        ]
        out[a] = statistics.median(hours) if hours else None
    return out


def reject_path_breakdown(entries: list[dict]) -> dict[str, int]:
    rejected = [e for e in entries if e["final_action"] == "rejected"]
    return {p: sum(1 for e in rejected if e.get("reject_path") == p) for p in REJECT_PATHS}


def overdue_counts(entries: list[dict], pending: list[dict], now: datetime, policy: dict) -> dict:
    """Decisions made after SLA, pending items past SLA, and pending URGENT escalations."""
    decided_late = sum(
        1 for e in entries
        if e.get("recommended_at") and sla_status(
            e["ai_action"], parse_ts(e["recommended_at"]), now, policy,
            decided_at=parse_ts(e["timestamp"]))["overdue"]
    )
    pending_status = [sla_status(r["action"], parse_ts(r["recommended_at"]), now, policy) for r in pending]
    return {
        "decided_late":    decided_late,
        "pending_overdue": sum(1 for s in pending_status if s["overdue"]),
        "escalations":     sum(1 for s in pending_status if s["escalate"]),
    }


def autonomy_status(stats: dict, policy: dict) -> dict[str, str]:
    """Per tier: is it a candidate for sampled review under the policy assumption?"""
    rule = policy["autonomy"]
    out = {}
    for tier, r in stats["by_tier"].items():
        if r["n"] < rule["min_decisions"]:
            out[tier] = f"Not enough evidence — {r['n']} of {rule['min_decisions']} decisions needed"
        elif r["approval_rate"] >= rule["min_approval_rate"] and r["edited"] <= rule["max_edits"]:
            out[tier] = (f"Candidate for sampled review — {r['approval_rate']:.0%} approved, "
                         f"{r['edited']} edits over {r['n']} decisions")
        else:
            out[tier] = (f"Keep full review — {r['approval_rate']:.0%} approved, "
                         f"{r['edited']} edits over {r['n']} decisions")
    return out
