"""
app.py — Supply Chain persona views (Planner · Executive · AI Ops & Trust).

Data sources (all produced by this repo — nothing is seeded or simulated):
  data/supply_chain_data.csv   public Kaggle dataset
  demo_output.json             last pipeline run (python demo_output.py)
  logs/run_log.jsonl           one line per pipeline run
  logs/decisions_log.jsonl     one line per reviewer action in this UI

Run: streamlit run app.py   (open a tab directly with ?tab=planner|exec|ops)
"""
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analytics import (
    ACTION_NOW, ACTION_ORDER, DEMAND_PERIOD_DAYS, compare_suppliers, compute_risk, load_dataset,
    portfolio_kpis, risk_flags, supplier_lead_time, timing, top_supplier_share,
)
from src.review import (
    DECISIONS_LOG, append_entry, autonomy_status, build_queue, decision_outcomes, decision_stats,
    format_duration, load_policy, make_entry, next_version, now_local, overdue_counts,
    parse_ts, read_jsonl, recommendation_id, reject_path_breakdown, sla_status,
    time_to_decision_hours,
)
from src.run_log import RUN_LOG

st.set_page_config(page_title="Supply Chain Persona Views", page_icon="📦", layout="wide")

DATA_CSV   = Path("data/supply_chain_data.csv")
CACHE_JSON = Path("demo_output.json")

BANNER = "Prototype on public Kaggle data — not production"
DEMAND_NOTE = (
    f"Demand assumption: daily demand = 'Number of products sold' ÷ {DEMAND_PERIOD_DAYS} days. "
    "The dataset does not state the sales period; under a 365-day assumption, "
    "at-risk SKUs fall from 85 to 27 of 100."
)
TIER_LABEL = {"URGENT_REORDER": "URGENT REORDER", "REORDER": "REORDER", "MONITOR": "MONITOR"}
TIER_COLOR = {"URGENT_REORDER": "red", "REORDER": "orange", "MONITOR": "green"}
NOT_MEASURED = "Not measured in prototype"

# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

if not DATA_CSV.exists():
    st.error("**Dataset not found** — `data/supply_chain_data.csv` is missing.")
    st.stop()


@st.cache_data
def _dataset() -> pd.DataFrame:
    return load_dataset()


df       = _dataset()
policy   = load_policy()
now      = now_local()
risk_df  = compute_risk(df).set_index("SKU")
revenue  = df.set_index("SKU")["Revenue generated"].to_dict()

demo = json.loads(CACHE_JSON.read_text(encoding="utf-8")) if CACHE_JSON.exists() else None
decisions      = demo.get("decisions", []) if demo else []
run_id         = (demo or {}).get("run_id") or "unlogged-run"
recommended_at = (demo or {}).get("generated_at")

entries, bad_decision_lines = read_jsonl(DECISIONS_LOG)
runs, bad_run_lines         = read_jsonl(RUN_LOG)
run_entries = [e for e in entries if e.get("run_id") == run_id]
pending, deferred = build_queue(decisions, run_id, recommended_at, entries, revenue, now.date())


def _timing_for(sku: str, product_type: str, supplier: str) -> dict:
    row = df[df["SKU"] == sku].iloc[0]
    demand = round(row["Number of products sold"] / DEMAND_PERIOD_DAYS, 2)
    return timing(row["Stock levels"], demand, supplier_lead_time(df, product_type, supplier))


decision_timing = {
    d["sku"]: _timing_for(d["sku"], d["product_type"], d["recommended_supplier"]) for d in decisions
}
late_count = sum(1 for t in decision_timing.values() if t["in_time"] is False)


def banner():
    st.warning(f"**{BANNER}**", icon="⚠️")


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def tier_badge(action: str):
    st.badge(TIER_LABEL[action], color=TIER_COLOR[action])


# ---------------------------------------------------------------------------
# Tabs (?tab= preselects one — used by scripts/take_screenshots.py)
# ---------------------------------------------------------------------------

TABS = {"planner": "Planner workbench", "exec": "Executive view", "ops": "AI Ops & Trust"}
default_tab = TABS.get(st.query_params.get("tab", "planner"), TABS["planner"])
tab_planner, tab_exec, tab_ops = st.tabs(list(TABS.values()), default=default_tab)


# ===========================================================================
# A. PLANNER WORKBENCH — "What must I decide today?"
# ===========================================================================

def _record(entry: dict):
    append_entry(entry)
    st.toast(f"Logged: {entry['sku']} {entry['final_action']}")
    st.rerun()


def render_card(rec: dict, reviewer: str):
    sku, action = rec["sku"], rec["action"]
    t = _timing_for(sku, rec["product_type"], rec["supplier"])
    sla = sla_status(action, parse_ts(rec["recommended_at"]), now, policy)
    risk = risk_df.loc[sku]
    key = rec["recommendation_id"]

    with st.container(border=True):
        head, side = st.columns([3, 2])
        with head:
            tier_badge(action)
            st.markdown(f"#### {sku} · {rec['product_type']}")
            lead = f"~{t['lead_time']:.1f} days lead time" if t["lead_time"] is not None else "lead time unknown"
            st.markdown(f"**Order {rec['qty']} units from {rec['supplier']}, {lead}**")
            if rec["version"] > 1:
                st.caption(f"Version {rec['version']} · {rec['source']} · linked to "
                           f"`{rec['linked_recommendation_id']}` · excluded: {', '.join(rec['excluded'])}")
            if rec.get("recheck_date"):
                st.caption(f"Deferred item — re-check date {rec['recheck_date']} reached")
        with side:
            (st.error if t["in_time"] is False else st.success)(f"Timing: {t['status']}")
            if action == "MONITOR" and t["in_time"] is False:
                st.caption("Known limitation: urgency does not yet use days of cover vs lead time (see README)")
            if sla["overdue"]:
                st.error(f"SLA: {sla['label']}")
            else:
                st.info(f"SLA: {sla['label']}")
            if sla["escalate"]:
                st.error("**Escalate to supervisor** — URGENT item past SLA "
                         "(display only: no notification sent, nothing auto-approved)")

        flags = risk_flags(risk) or ["none"]
        why = [f"Risk {risk['risk_level']} (score {int(risk['risk_score'])} of 7)",
               f"flags: {', '.join(flags)}"]
        if int(risk["Stock levels"]) == 0:
            why.append("stock = 0 → URGENT")
        st.markdown("**Why it needs review:** " + " · ".join(why) +
                    f" · recorded revenue ${revenue.get(sku, 0):,.0f} (dataset field, not lost-sales forecast)")
        since = (f"{format_duration(now - parse_ts(rec['recommended_at']))} ago"
                 if rec["recommended_at"] else "time not recorded")
        st.caption(f"Recommended {since} · `{key}`")

        with st.expander("Evidence"):
            m = st.columns(5)
            m[0].metric("Stock", f"{int(risk['Stock levels'])} units")
            m[1].metric("Days of cover", f"{t['days_of_cover']:.2f} d")
            m[2].metric("Recommended supplier lead time", "—" if t["lead_time"] is None else f"{t['lead_time']:.1f} d")
            m[3].metric("Gap (cover − lead)", "—" if t["gap_days"] is None else f"{t['gap_days']:+.1f} d")
            m[4].metric("Current supplier", risk["Supplier name"])
            comp = compare_suppliers(df, sku, risk["risk_level"], exclude=rec["excluded"])
            if comp is not None:
                if (comp["best_supplier"], comp["order_quantity"], comp["action"]) != (rec["supplier"], rec["qty"], action):
                    st.warning(
                        f"Deterministic check differs from this recommendation: "
                        f"{comp['action']} · {comp['best_supplier']} · {comp['order_quantity']} units."
                    )
                table = comp["ranking"][["Supplier name", "avg_lead_time", "avg_defect_rate",
                                         "avg_manufacturing_cost", "composite_score"]]
                table.columns = ["Supplier", "Avg lead time (d)", "Avg defect rate (%)",
                                 "Avg mfg cost ($)", "Composite score (lower = better)"]
                w = comp["weights"]
                st.caption(f"Supplier comparison for {rec['product_type']} — weights {comp['action']}: "
                           f"lead time {w[0]:.0%} · defect {w[1]:.0%} · cost {w[2]:.0%}")
                st.dataframe(table, hide_index=True, width="stretch")
            st.markdown(f"**Rejected suppliers:** {', '.join(rec['rejected_suppliers']) or '—'}")
            if rec["reason_text"]:
                st.caption(f"AI reason: {rec['reason_text']}")

        choice = st.radio("Decision", ["Approve", "Edit qty", "Reject"], horizontal=True, key=f"c-{key}")
        kw = {}
        if choice == "Edit qty":
            kw["final_qty"] = st.number_input("New quantity", min_value=1, value=int(rec["qty"]), key=f"q-{key}")
            kw["reason"] = st.text_input("Reason (required)", key=f"r-{key}")
        elif choice == "Reject":
            kw["reason"] = st.text_input("Reason (required)", key=f"r-{key}")
            paths = {"Manual order": "manual_order", "Recompute with constraint": "recompute"}
            if action not in policy.get("no_defer_tiers", []):
                paths["Defer with re-check date"] = "defer"
            else:
                st.caption(f"Defer is not allowed for {TIER_LABEL[action]}.")
            path_label = st.radio("Next step (required)", list(paths), horizontal=True, key=f"p-{key}")
            kw["reject_path"] = paths[path_label]
            if kw["reject_path"] == "manual_order":
                suppliers = sorted(df["Supplier name"].unique())
                kw["final_supplier"] = st.selectbox("Supplier", suppliers, key=f"s-{key}")
                kw["final_qty"] = st.number_input("Quantity", min_value=1, value=int(rec["qty"]), key=f"mq-{key}")
            elif kw["reject_path"] == "recompute":
                options = list(compare_suppliers(df, sku, risk["risk_level"])["ranking"]["Supplier name"])
                exclude = st.multiselect("Exclude suppliers", options,
                                         default=sorted(set(rec["excluded"]) | {rec["supplier"]}), key=f"x-{key}")
                st.caption("Recomputes with the deterministic supplier comparison for this SKU only (no LLM). "
                           "The new version returns to the queue, linked to this one.")
            else:
                kw["recheck_date"] = st.date_input("Re-check date", value=now.date() + timedelta(days=7),
                                                   min_value=now.date(), key=f"d-{key}")

        if st.button("Record decision", key=f"b-{key}", type="primary"):
            try:
                if kw.get("reject_path") == "recompute":
                    new = compare_suppliers(df, sku, risk["risk_level"], exclude=exclude)
                    if new is None:
                        raise ValueError("No supplier left after exclusions — exclude fewer suppliers.")
                    version = next_version(rec, entries)
                    kw["recomputed"] = {
                        "recommendation_id": recommendation_id(run_id, sku, version),
                        "version": version, "action": new["action"], "supplier": new["best_supplier"],
                        "qty": new["order_quantity"], "product_type": new["product_type"],
                        "rejected_suppliers": new["rejected"], "excluded": exclude,
                    }
                final = {"Approve": "approved", "Edit qty": "edited", "Reject": "rejected"}[choice]
                _record(make_entry(rec=rec, reviewer=reviewer, final_action=final, policy=policy, **kw))
            except ValueError as e:
                st.error(str(e))


with tab_planner:
    banner()
    if not decisions:
        st.info("No recommendations yet — run the pipeline: `python demo_output.py`")
    else:
        urgent_pending = sum(1 for r in pending if r["action"] == "URGENT_REORDER")
        overdue_pending = sum(1 for r in pending
                              if sla_status(r["action"], parse_ts(r["recommended_at"]), now, policy)["overdue"])
        st.markdown(f"### {len(pending)} decisions waiting — {urgent_pending} URGENT. "
                    f"{late_count} of {len(decisions)} SKUs run out of stock before delivery.")
        k = st.columns(4)
        k[0].metric("Pending decisions", f"{len(pending)} in queue",
                    help=f"{len(run_entries)} decisions already logged for run {run_id}", border=True)
        k[1].metric("URGENT pending", f"{urgent_pending} of {len(pending)}", border=True)
        k[2].metric("Past review SLA", f"{overdue_pending} of {len(pending)}",
                    help="SLA per tier from config/policy.yaml (policy assumption)", border=True)
        k[3].metric("Stock runs out before delivery", f"{late_count} of {len(decisions)}",
                    help="gap_days = days of cover − recommended supplier lead time ≤ 0", border=True)
        st.caption(DEMAND_NOTE)
        st.caption("SLA values and review modes are a policy assumption (config/policy.yaml). "
                   f"Recommendations from run `{run_id}`"
                   + (f", generated {recommended_at}." if recommended_at else
                      " — generation time not recorded (re-run the pipeline to enable SLA tracking)."))

        reviewer = st.text_input("Reviewer name (required to record a decision)", key="reviewer")

        for tier in ACTION_ORDER:
            items = [r for r in pending if r["action"] == tier]
            mode = policy["review_mode"][tier]
            st.subheader(f"{TIER_LABEL[tier]} — {len(items)} pending · "
                         f"{'review one by one' if mode == 'one_by_one' else 'batch approval allowed'}")
            if not items:
                st.caption("Nothing pending in this tier.")
                continue
            if mode == "batch_allowed" and len(items) > 1:
                with st.container(border=True):
                    st.markdown(f"**Batch approve all {len(items)} {TIER_LABEL[tier]} items** "
                                f"({', '.join(r['sku'] for r in items)})")
                    confirmed = st.checkbox("I have reviewed every item in this list", key=f"bc-{tier}")
                    if st.button(f"Approve {len(items)} items", key=f"bb-{tier}", disabled=not confirmed):
                        try:
                            batch = [make_entry(rec=r, reviewer=reviewer, final_action="approved",
                                                policy=policy, batch=True) for r in items]
                            for e in batch:
                                append_entry(e)
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
            for rec in items:
                render_card(rec, reviewer)

        if deferred:
            with st.expander(f"Deferred — {len(deferred)} item(s) return on their re-check date"):
                st.dataframe(pd.DataFrame([{"SKU": r["sku"], "Tier": TIER_LABEL[r["action"]],
                                            "Re-check date": r["recheck_date"]} for r in deferred]),
                             hide_index=True, width="stretch")


# ===========================================================================
# B. EXECUTIVE VIEW — "Is the AI helping the business?"
# ===========================================================================

with tab_exec:
    banner()
    k = portfolio_kpis(df)
    share_now = top_supplier_share(decisions, actions=ACTION_NOW)
    share_all = top_supplier_share(decisions)
    outcomes = decision_outcomes(decisions, run_id, entries)

    def share_text(s: dict) -> str:
        return f"{s['supplier']} carries {s['units']} of {s['total_units']} units ({s['share']:.0%})"

    headline = (f"### {k['at_risk_skus']} of {k['total_skus']} SKUs flagged at risk "
                f"under a {DEMAND_PERIOD_DAYS}-day demand assumption")
    if share_now:
        headline += f"; {share_text(share_now)} in orders needing action now."
    st.markdown(headline)

    r1 = st.columns(3)
    r1[0].metric("At-risk SKUs", f"{k['at_risk_skus']} of {k['total_skus']}",
                 help="Stock below demand over the SKU's lead time", border=True)
    r1[1].metric("Zero-stock SKUs", f"{k['zero_stock_skus']} of {k['total_skus']}", border=True)
    r1[2].metric("Revenue of at-risk SKUs", f"${k['at_risk_revenue']:,.0f}",
                 delta=f"{k['at_risk_revenue'] / k['total_revenue']:.0%} of dataset revenue", delta_color="off", delta_arrow="off",
                 help="Dataset revenue field ('Revenue generated') — not a forecast of lost sales", border=True)
    r2 = st.columns(3)
    r2[0].metric("Decisions already late", f"{late_count} of {len(decisions)}",
                 help="Stock runs out before the recommended supplier can deliver", border=True)
    r2[1].metric("Decisions approved / overridden / pending",
                 f"{outcomes['approved']} approved · {outcomes['overridden']} overridden",
                 delta=f"{outcomes['pending']} pending", delta_color="off", delta_arrow="off",
                 help=f"Each of the {len(decisions)} AI decisions counted once, from logs/decisions_log.jsonl "
                      f"for run {run_id}. Overridden = quantity edited or rejected (any next step).",
                 border=True)
    if share_now:
        r2[2].metric("Top supplier share — orders needing action now",
                     f"{share_now['supplier']}: {share_now['units']} of {share_now['total_units']} units",
                     delta=f"{share_now['share']:.0%} of URGENT_REORDER + REORDER units", delta_color="off",
                     delta_arrow="off", border=True)
        if share_all:
            r2[2].caption(f"incl. MONITOR orders: {share_text(share_all)}")
    else:
        r2[2].metric("Top supplier share — orders needing action now", "—",
                     help="No URGENT_REORDER or REORDER recommendations", border=True)
    st.caption(DEMAND_NOTE)

    st.subheader("Portfolio risk (Agent 4)")
    if not demo:
        st.info("No pipeline output yet — run `python demo_output.py`.")
    else:
        run = next((r for r in runs if r.get("run_id") == run_id), None)
        g = ((run or {}).get("tasks") or [{}])[-1].get("guardrail") if run else None
        if g:
            st.caption(f"Passed the fact-check guardrail in run `{run_id}` after {g['calls']} attempt(s).")
        else:
            st.caption("Guardrail result not recorded for this output (generated before run logging).")
        for risk in demo.get("concentration_risks", []):
            st.error(f"Concentration risk — {risk}")
        st.info(f"**Executive summary** — {demo.get('executive_summary', '')}")

    with st.container(border=True):
        st.markdown(f"**Value vs baseline — {NOT_MEASURED}**")
        st.caption("No real baseline: there is no record of what planners decided without the AI on this data.")


# ===========================================================================
# C. AI OPS & TRUST — "Should agents get more or less autonomy?"
# ===========================================================================

with tab_ops:
    banner()
    st.subheader("Pipeline runs")
    if bad_run_lines or bad_decision_lines:
        st.warning(f"Skipped malformed log lines: run_log {bad_run_lines}, decisions_log {bad_decision_lines}.")
    if not runs:
        st.info("No pipeline runs logged yet — `logs/run_log.jsonl` is created by `python demo_output.py`.")
    else:
        ids = [r["run_id"] for r in reversed(runs)]
        sel = st.selectbox("Run", ids, index=0, help="Most recent first")
        run = next(r for r in runs if r["run_id"] == sel)
        tasks = run.get("tasks", [])
        gs = [t["guardrail"] for t in tasks if t.get("guardrail")]
        first_pass = sum(1 for g in gs if g["first_pass"])
        retries = sum(g["retries"] for g in gs)
        cpd = run.get("cost_per_decision_usd")

        st.markdown(f"### Estimated LLM token cost ${cpd:.4f} per decision · guardrails passed first time on "
                    f"{first_pass} of {len(tasks)} tasks" if cpd is not None else
                    f"### Run {run['status']} — LLM token cost per decision not available")
        m = st.columns(4)
        m[0].metric("LLM token cost per decision (est.)", "—" if cpd is None else f"${cpd:.4f}",
                    delta=(f"${run['estimated_cost_usd']:.4f} per run · {run['n_decisions']} decisions"
                           if run.get("estimated_cost_usd") is not None else None), delta_color="off", delta_arrow="off",
                    help=f"{run['model']} prices as of {run.get('prices_as_of')} ({run.get('price_source')})",
                    border=True)
        m[1].metric("Run duration", f"{run['duration_s']:.0f} s", delta=f"status: {run['status']}",
                    delta_color="off", delta_arrow="off", border=True)
        m[2].metric("Guardrail first-pass", f"{first_pass} of {len(tasks)} tasks", border=True)
        m[3].metric("Guardrail retries", f"{retries} {'retry' if retries == 1 else 'retries'}", delta=f"across {len(tasks)} tasks",
                    delta_color="off", delta_arrow="off", border=True)
        if run.get("error"):
            st.error(f"Run error: {run['error']}")

        st.dataframe(pd.DataFrame([{
            "Task": t["task"],
            "Duration (s)": t["duration_s"],
            "Guardrail calls": (t.get("guardrail") or {}).get("calls", 0),
            "Passed": (t.get("guardrail") or {}).get("passed", 0),
            "Failed": (t.get("guardrail") or {}).get("failed", 0),
            "Retries": (t.get("guardrail") or {}).get("retries", 0),
        } for t in tasks]), hide_index=True, width="stretch")
        with st.expander("Token usage (CrewAI usage metrics)"):
            u = run.get("token_usage", {})
            st.markdown(
                f"Prompt {u.get('prompt_tokens', 0):,} (cached {u.get('cached_prompt_tokens', 0):,}) · "
                f"completion {u.get('completion_tokens', 0):,} · total {u.get('total_tokens', 0):,} · "
                f"{u.get('successful_requests', 0)} requests · model `{run['model']}` · "
                f"CrewAI {run['crewai_version']}")
            st.caption(f"Cost is an estimate from config/pricing.yaml — prices as of {run.get('prices_as_of')}.")

    st.subheader("Human review")
    stats = decision_stats(entries)
    if not entries:
        st.info(f"Override rate, edit rate and time-to-decision: **{NOT_MEASURED}** yet — "
                "no reviewer decisions logged. They appear when a person records decisions in the Planner workbench.")
    else:
        ov = overdue_counts(entries, pending, now, policy)
        o = stats["overall"]
        m = st.columns(4)
        m[0].metric("Override rate", pct(o["override_rate"]), delta=f"{o['edited'] + o['rejected']} of {o['n']} decisions",
                    delta_color="off", delta_arrow="off", help="Edited or rejected ÷ all decisions", border=True)
        m[1].metric("Edit rate", pct(o["edit_rate"]), delta=f"{o['edited']} of {o['n']} decisions",
                    delta_color="off", delta_arrow="off", border=True)
        m[2].metric("Decided after SLA", f"{ov['decided_late']} of {o['n']}", border=True)
        m[3].metric("Pending past SLA / escalations", f"{ov['pending_overdue']} / {ov['escalations']}",
                    help="Escalations = pending URGENT items past SLA (display only)", border=True)

        ttd = time_to_decision_hours(entries)
        st.dataframe(pd.DataFrame([{
            "Tier": TIER_LABEL[a],
            "Decisions": stats["by_tier"][a]["n"],
            "Approval rate": pct(stats["by_tier"][a]["approval_rate"]),
            "Override rate": pct(stats["by_tier"][a]["override_rate"]),
            "Edit rate": pct(stats["by_tier"][a]["edit_rate"]),
            "Median time to decision": "—" if ttd[a] is None else format_duration(timedelta(hours=ttd[a])),
        } for a in ACTION_ORDER]), hide_index=True, width="stretch")
        rp = reject_path_breakdown(entries)
        st.markdown("**Reject paths:** " + " · ".join(f"{p.replace('_', ' ')} {n}" for p, n in rp.items()))
        with st.expander(f"Audit trail — {len(entries)} logged decisions (logs/decisions_log.jsonl)"):
            st.dataframe(pd.DataFrame(entries), hide_index=True, width="stretch")

    st.subheader("Autonomy policy — policy assumption")
    st.markdown(policy["autonomy"]["rule"])
    st.dataframe(pd.DataFrame([{"Tier": TIER_LABEL[a], "Status": s}
                               for a, s in autonomy_status(stats, policy).items()]),
                 hide_index=True, width="stretch")

    st.subheader("Re-run triggers")
    st.markdown(policy["rerun_triggers"])
