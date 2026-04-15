# Supply Chain Intelligence Agent

**85 out of 100 SKUs** in a real beauty/FMCG dataset are at risk of stockout right now.
This system finds them, scores their supplier risk, and recommends exactly which supplier to reorder from — fully automated, end-to-end, in a single command.

*Portfolio project built on a public Kaggle supply chain dataset ([Supply Chain Analysis](https://www.kaggle.com/datasets/harshsingh2209/supply-chain-analysis)).*

---

## Real Output Example

```
SKU68 | haircare | stock = 0 units | daily demand = 5.43 units/day

Action            : URGENT_REORDER
Recommended       : Supplier 4  (lead time 14.5d — fastest available)
Order quantity    : 119 units   [calc: ceil(5.43 × 14.5d × 1.5)]
Reason            : No stock remaining; Supplier 4 selected under URGENT weights
                    (lead time 70%) — delivers 7 days faster than Supplier 3.
Rejected suppliers: Supplier 5, Supplier 3, Supplier 2, Supplier 1
```

> Without urgency-aware weighting, the system would have chosen Supplier 3 (best defect rate, but slowest lead time at 21.4 days). SKU68 has zero stock — speed wins.

Agent 4 then synthesises all 5 decisions as a portfolio (BLUF format — executive summary first):

```
  ### 1. Executive Summary
  The procurement portfolio reveals significant concentration risk: Supplier 3
  handles multiple urgent haircare SKUs, creating a single point of failure.
  Immediate action should diversify reorder decisions across at least two
  suppliers wherever lead times allow.

  ### 2. Critical Warnings
  ! Supplier 3 is the recommended supplier for 2 REORDER SKUs — any disruption
    to Supplier 3 would require emergency re-sourcing for multiple products.

  ### 3. Key Insights
  - 2 of 3 haircare SKUs rely on Supplier 3 — supplier concentration raises
    risk if Supplier 3 faces a disruption.
  - 2 of 3 haircare SKUs are high-priority orders — haircare category is
    over-represented in urgent decisions.
  - SKU2 and SKU24 share the same supplier and lead time window — a single
    delay event would affect both simultaneously.
```

---

## How It Works — 4 Agents in Plain English

**Agent 1 — Supply Chain Data Analyst**
Reads the full dataset (100 SKUs) and identifies which products are at risk of running out before the next replenishment arrives. It calculates days of stock remaining versus the supplier's lead time, then ranks the top 5 urgent SKUs by revenue density so the highest-value stockouts are always actioned first.
*Output: ranked list of at-risk SKUs with stock levels, days remaining, and revenue.*

**Agent 2 — Supply Chain Risk Analyst**
Takes the at-risk SKUs from Agent 1 and scores each one for supplier and quality risk. It looks at three signals — lead time, defect rate, and inspection results — and assigns every SKU a risk level of Critical, High, or Medium using industry-standard thresholds (AQL 2.5% for defects, FMCG 14-day cycle for lead time).
*Output: structured risk report with score, flags, and classification per SKU.*

**Agent 3 — Procurement Recommendation Agent**
For every SKU in the risk report, it calls the Supplier Comparison Tool once per SKU, passing the risk level as context. The tool selects scoring weights based on urgency — if a SKU has zero stock it prioritises speed; if it just needs monitoring it prioritises cost — then calculates the recommended supplier, order quantity, and action code.
*Output: Pydantic-validated reorder decisions with action, supplier, quantity, and rejected alternatives.*

**Agent 4 — Portfolio Risk Synthesiser**
Receives the full set of reorder decisions from Agent 3 and reasons across all of them simultaneously — no tools, pure LLM analysis. It surfaces patterns that are invisible when SKUs are examined one at a time: which suppliers are being relied on for multiple urgent SKUs, which product categories are over-represented in the risk list, and whether the combined exposure creates a systemic risk that individual decisions don't reveal.
*Output: PortfolioInsight (Pydantic) with cross-SKU patterns, concentration risks, and an executive summary.*

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     src/main.py  →  SupplyChainCrew             │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ Process.sequential
          ┌───────────────────────▼────────────────────────┐
          │           AGENT 1 — Data Analyst               │
          │  Tool : Inventory Analysis Tool                 │
          │  Input: product_type="all"  (100 SKUs)          │
          │  Logic: days_of_stock < stock_needed → at risk  │
          │         rank by revenue_density                 │
          │  Guard: must contain real SKU codes + count=100 │
          │  Out  : top-5 at-risk SKUs + revenue context    │
          └───────────────────────┬────────────────────────┘
                                  │ context passed →
          ┌───────────────────────▼────────────────────────┐
          │           AGENT 2 — Risk Analyst                │
          │  Tool : Risk Detection Tool                     │
          │  Input: "SKU2,SKU24,SKU68,SKU16,SKU34"          │
          │  Logic: lead_time + defect_rate + inspection    │
          │         → score 0-7 → Critical/High/Medium      │
          │  Guard: only valid risk levels allowed          │
          │  Out  : RiskDetectionOutput (Pydantic)          │
          └───────────────────────┬────────────────────────┘
                                  │ context passed →
          ┌───────────────────────▼────────────────────────┐
          │        AGENT 3 — Recommendation Agent           │
          │  Tool : Supplier Comparison Tool (1 call/SKU)   │
          │  Input: sku="SKU68", risk_level="High"          │
          │  Logic: action → weight profile → composite     │
          │         score → best supplier → order_qty       │
          │  Guard: valid supplier + action + qty > 0       │
          │         + stock=0 must be URGENT_REORDER        │
          │  Out  : ReorderReport (Pydantic)                │
          └───────────────────────┬────────────────────────┘
                                  │ context passed →
          ┌───────────────────────▼────────────────────────┐
          │      AGENT 4 — Portfolio Risk Synthesiser        │
          │  Tool : none — pure LLM reasoning               │
          │  Input: all decisions from Agent 3              │
          │  Logic: cross-SKU pattern analysis              │
          │         supplier concentration + category risk  │
          │  Guard: must reference ≥ 2 distinct SKU codes   │
          │  Out  : PortfolioInsight (Pydantic)             │
          └────────────────────────────────────────────────┘
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Agent framework | [CrewAI](https://github.com/crewAIInc/crewAI) |
| LLM | OpenAI GPT-4o-mini |
| Runtime | Python 3.11 |
| Output validation | Pydantic v2 |
| Data processing | pandas |
| Agent / task config | YAML (`config/agents.yaml`, `config/tasks.yaml`) |
| Demo dashboard | Streamlit (`app.py`) |

---

## Local Demo Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add your OpenAI key
echo "OPENAI_API_KEY=sk-..." > .env

# 3. Run the pipeline — generates demo_output.json
python demo_output.py

# 4. Launch the dashboard
streamlit run app.py
```

The dashboard reads from `demo_output.json` — no live API calls during the demo.

## How to Run (CLI)

```bash
# Full pipeline with verbose agent logs
python -m src.main

# Clean summary output — no verbose logs
python demo_output.py
```

---

## Design Decisions

### Defect rate thresholds — why 2% and 4%?

Thresholds follow the **AQL 2.5 standard** (Acceptable Quality Level), the international benchmark for consumer goods inspection (ISO 2859-1). A defect rate above 2% signals the supplier is approaching the tolerance boundary; above 4% means they have already breached it. These are not arbitrary numbers — they are the same thresholds used in audits across the FMCG and beauty industry.

| Defect rate | Signal | Score |
|---|---|---|
| ≤ 2.0% | Within AQL tolerance | +0 |
| 2.0% – 4.0% | Approaching limit | +1 |
| > 4.0% | AQL breach | +2 |

### Lead time thresholds — why 10 days and 20 days?

Thresholds are anchored to the **FMCG standard replenishment cycle of 14 days**. A lead time under 10 days is safe — the supplier delivers well within the cycle. Between 10 and 20 days means the order must be placed immediately to avoid a gap. Above 20 days means the supplier cannot reliably fit inside a single replenishment window, making any stockout event difficult to recover from in time.

| Lead time | Signal | Score |
|---|---|---|
| ≤ 10 days | Fits within cycle | +0 |
| 10 – 20 days | Tight window | +1 |
| > 20 days | Exceeds cycle | +2 |

### Supplier scoring weights — why do they change per urgency level?

A static composite score would always optimise for the same priorities regardless of context. But procurement decisions are not context-free. When a SKU has zero stock, the only thing that matters is how fast the supplier can deliver. When a SKU just needs monitoring, cost efficiency matters more than speed.

The system determines the **action first** (from stock level + risk classification), then selects the matching weight profile before ranking suppliers:

| Action | Situation | Lead time | Defect rate | Mfg cost |
|---|---|---|---|---|
| `URGENT_REORDER` | Stock = 0 or Critical risk | **70%** | 20% | 10% |
| `REORDER` | High risk, restock this week | 40% | **40%** | 20% |
| `MONITOR` | Medium risk, next cycle | 20% | 30% | **50%** |

This is what caused the Supplier 3 vs Supplier 4 difference on SKU68: under balanced weights (40/40/20), Supplier 3 wins on defect rate. Under urgency weights (70/20/10), Supplier 4 wins because its lead time of 14.5 days is 7 days faster — which matters when the shelf is empty.

### Why does Agent 4 have no tools?

Agents 1–3 each use a tool because their core task is data retrieval or deterministic calculation — things the LLM should not guess. Agent 4's task is the opposite: synthesise patterns across decisions that are already in its context. There is no data to fetch and no formula to run. Giving it a tool would only introduce failure modes (wrong arguments, unnecessary calls) with no benefit. The LLM earns its place here precisely because cross-SKU pattern recognition — spotting that the same supplier appears across multiple urgent decisions, or that one category dominates the risk list — is the kind of reasoning LLMs are genuinely good at.

### Manufacturing cost vs shipping cost — why manufacturing cost drives supplier selection?

Both costs vary across suppliers in the dataset. Manufacturing cost spread across all 5 suppliers is **$21.09** (min $33.62, max $54.71). Shipping cost spread is only **$1.00** (min $5.34, max $6.34). Using shipping cost as a selection signal would create at most a $1 difference in the decision — well within noise. Manufacturing cost creates a meaningful $21 spread that is worth optimising, which is why the MONITOR weight profile allocates 50% to manufacturing cost and the supplier composite score uses it as the primary cost signal.

| Cost type | Min | Max | Spread | Used in scoring |
|---|---|---|---|---|
| Manufacturing cost | $33.62 | $54.71 | $21.09 | Yes (mfg_cost weight) |
| Shipping cost | $5.34 | $6.34 | $1.00 | No |
