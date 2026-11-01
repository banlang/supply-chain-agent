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
_INSPECTION_RESULTS = dict(zip(_supply_df["SKU"], _supply_df["Inspection results"]))


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

    return (True, output.raw)


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

    return (True, output.raw)


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
        return (True, output.raw)

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

    return (True, output.raw)


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUM      = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
_QUALIFIER = (
    r"(?:at-risk|high-risk|high-priority|urgent|critical|distinct|"
    r"haircare|skincare|cosmetics?|URGENT_REORDER|REORDER|MONITOR)"
)
_NOUN     = r"(?:SKUs?|decisions?|products?|items?|orders?)\b"
_SKU_LIST = r"(SKU\d+(?:\s*(?:,\s*(?:and\s+)?|and|&)\s*SKU\d+)*)"

# "3 of 5 SKUs", "2 out of the 3 haircare SKUs"
_X_OF_Y_RE = re.compile(
    rf"(?<![\d.])(?<!Supplier\s){_NUM}\s+(?:out\s+)?of\s+(?:the\s+|all\s+)?{_NUM}\s+"
    rf"(?:{_QUALIFIER}\s+)*{_NOUN}",
    re.IGNORECASE,
)
# "2 SKUs", "3 haircare SKUs" — optionally followed by "(SKU2, SKU24)"
_N_SKUS_RE = re.compile(
    rf"(?<![\d.])(?<!Supplier\s){_NUM}\s+(?:{_QUALIFIER}\s+)*{_NOUN}"
    rf"(?:\s*\(\s*{_SKU_LIST}\s*\))?",
    re.IGNORECASE,
)
# "SKU2, SKU24 rely on Supplier 3", "(SKU2 and SKU24) depend on Supplier 3"
_RELIANCE_RE = re.compile(
    rf"{_SKU_LIST}\s*\)?\s+(?:all\s+|both\s+)?"
    r"(?:rely|relies|depend|depends|are\s+sourced\s+from|is\s+sourced\s+from|"
    r"are\s+supplied\s+by|is\s+supplied\s+by|are\s+assigned\s+to|is\s+assigned\s+to)"
    r"\s+(?:on\s+|upon\s+)?(Supplier\s+\d+)",
    re.IGNORECASE,
)
_SHARE_RE = re.compile(r"\bshar(?:e|es|ed|ing)\b", re.IGNORECASE)
# "233 units", "a combined 1,000 total units"
_UNITS_RE = re.compile(r"(?<![\d.,])(\d[\d,]*)\s+(?:total\s+)?units\b", re.IGNORECASE)
# "Pending inspections", "failed inspection", "inspection=Fail"
_INSPECTION_RE = re.compile(
    r"\b(pending|fail(?:ed)?|pass(?:ed)?)\s+inspections?\b"
    r"|\binspections?\s*(?:=|:|results?\s+of|is|are)?\s*(pending|fail(?:ed)?|pass(?:ed)?)\b",
    re.IGNORECASE,
)

_PRODUCT_TYPES = ("haircare", "skincare", "cosmetics")
_ACTION_WORDS  = {
    "URGENT_REORDER": r"\bURGENT_REORDER\b|\burgent\b",
    "REORDER":        r"(?<!URGENT_)\bREORDER\b",
    "MONITOR":        r"\bMONITOR\b",
}


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def parse_decisions(raw: str) -> list[dict] | None:
    """Extract the decision dicts from a ReorderReport JSON string; None if unparseable."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        decisions = json.loads(match.group(0)).get("decisions")
    except (json.JSONDecodeError, AttributeError):
        return None
    return decisions or None


def _portfolio_statements(raw: str) -> list[str]:
    """Split a PortfolioInsight (JSON or free text) into individual sentences."""
    try:
        data  = json.loads(raw)
        parts = [*data.get("patterns", []), *data.get("concentration_risks", []),
                 data.get("executive_summary", "")]
    except (json.JSONDecodeError, AttributeError):
        parts = raw.splitlines()
    sentences = []
    for part in parts:
        sentences += [s.strip() for s in re.split(r"(?<=[.!?;])\s+", str(part)) if s.strip()]
    return sentences


def _subsets(sentence: str, decisions: list[dict]) -> list[list[dict]]:
    """
    Every group of decisions a sentence could be describing: the subsets formed
    by the product types, suppliers and actions it mentions (at most one filter
    per dimension; no filter = the full portfolio).
    """
    types     = [t for t in _PRODUCT_TYPES if re.search(rf"\b{t.rstrip('s')}s?\b", sentence, re.I)]
    suppliers = sorted(set(re.sub(r"\s+", " ", s) for s in re.findall(r"Supplier\s+\d+", sentence)))
    actions   = [a for a, pat in _ACTION_WORDS.items() if re.search(pat, sentence, re.I)]
    action_filters = [{a} for a in actions] + ([set(actions)] if len(actions) > 1 else [])

    subsets = []
    for t in [None, *types]:
        for s in [None, *suppliers]:
            for a in [None, *action_filters]:
                subsets.append([
                    d for d in decisions
                    if (t is None or d.get("product_type", "").lower() == t)
                    and (s is None or d.get("recommended_supplier") == s)
                    and (a is None or d.get("action") in a)
                ])
    return subsets


def _valid_counts(sentence: str, decisions: list[dict]) -> set[int]:
    """Every count a sentence could truthfully cite (see _subsets)."""
    return {len(s) for s in _subsets(sentence, decisions)}


def _valid_unit_totals(sentence: str, decisions: list[dict]) -> set[int]:
    """
    Every order-quantity total a sentence could truthfully cite: the subsets
    from _subsets, the SKUs it names together, or any single SKU.
    """
    named   = set(re.findall(r"\bSKU\d+\b", sentence))
    subsets = _subsets(sentence, decisions) + [[d for d in decisions if d.get("sku") in named]]
    subsets += [[d] for d in decisions]
    return {sum(int(d.get("order_quantity", 0)) for d in s) for s in subsets}


def _ground_truth(decisions: list[dict]) -> str:
    """One-line summary of the real decision counts, included in every correction."""
    def group(key: str) -> str:
        buckets: dict[str, list[str]] = {}
        for d in decisions:
            buckets.setdefault(d.get(key, "?"), []).append(d.get("sku", "?"))
        return "; ".join(f"{k}={len(v)} ({', '.join(v)})" for k, v in sorted(buckets.items()))

    return (
        f"Ground truth from the reorder decisions — {len(decisions)} SKUs total. "
        f"By product_type: {group('product_type')}. "
        f"By recommended_supplier: {group('recommended_supplier')}. "
        f"By action: {group('action')}."
    )


def check_portfolio_facts(raw: str, decisions: list[dict]) -> list[str]:
    """
    Fact-check Agent 4's text against Agent 3's decisions. Returns a list of
    specific errors (empty if every checkable claim is correct):
      1. Every SKU code mentioned exists in the decisions
      2. "X of Y" / "N SKUs" counts match a real subset count
         (by product_type, recommended_supplier or action named in the sentence)
      3. "N SKUs (SKUa, SKUb)" — N equals the number of SKUs listed
      4. "SKUa, SKUb rely on Supplier N" — matches each SKU's recommended_supplier
      5. A "shared" signal must name at least 2 SKUs that share it
      6. "N units" totals equal the summed order_quantity of the SKUs described
      7. Inspection-status claims match the dataset's Inspection results
    """
    by_sku = {d.get("sku"): d for d in decisions}
    errors = []

    unknown = sorted(set(re.findall(r"\bSKU\d+\b", raw)) - set(by_sku), key=lambda s: int(s[3:]))
    if unknown:
        errors.append(
            f"{', '.join(unknown)} not in the reorder decisions — "
            f"only cite {', '.join(by_sku)}."
        )

    for sentence in _portfolio_statements(raw):
        valid  = _valid_counts(sentence, decisions)
        masked = sentence

        for m in _X_OF_Y_RE.finditer(sentence):
            x, y = _to_int(m.group(1)), _to_int(m.group(2))
            if y not in valid or x not in valid or x > y:
                errors.append(f"'{m.group(0)}' is wrong (in: \"{sentence}\").")
            masked = masked.replace(m.group(0), " ")

        for m in _N_SKUS_RE.finditer(masked):
            n, listed = _to_int(m.group(1)), m.group(2)
            if listed and n != len(re.findall(r"SKU\d+", listed)):
                errors.append(f"'{m.group(0)}' — the count does not match the SKUs listed.")
            elif n not in valid:
                errors.append(f"'{m.group(0)}' is wrong (in: \"{sentence}\").")

        for m in _RELIANCE_RE.finditer(sentence):
            supplier = re.sub(r"\s+", " ", m.group(2))
            wrong = [
                f"{sku} uses {by_sku[sku]['recommended_supplier']}"
                for sku in re.findall(r"SKU\d+", m.group(1))
                if sku in by_sku and by_sku[sku].get("recommended_supplier") != supplier
            ]
            if wrong:
                errors.append(f"'{m.group(0)}' is wrong: {'; '.join(wrong)}.")

        units_ok = _valid_unit_totals(sentence, decisions)
        for m in _UNITS_RE.finditer(sentence):
            if int(m.group(1).replace(",", "")) not in units_ok:
                errors.append(
                    f"'{m.group(0)}' is wrong (in: \"{sentence}\") — "
                    f"unit totals must be the sum of order_quantity for the SKUs described."
                )

        skus_here = set(re.findall(r"\bSKU\d+\b", sentence))
        statuses  = {(a or b).lower()[:4] for a, b in _INSPECTION_RE.findall(sentence)}
        if len(statuses) == 1:
            claimed = {"pend": "Pending", "fail": "Fail", "pass": "Pass"}[statuses.pop()]
            wrong = [
                f"{sku} is {_INSPECTION_RESULTS[sku]}" for sku in sorted(skus_here)
                if sku in _INSPECTION_RESULTS and _INSPECTION_RESULTS[sku] != claimed
            ]
            if wrong:
                errors.append(
                    f"\"{sentence}\" — inspection claim is wrong: {'; '.join(wrong)}. "
                    f"Inspection results are not in your context; do not cite them."
                )

        if _SHARE_RE.search(sentence) and len(skus_here) == 1:
            errors.append(
                f"\"{sentence}\" claims a shared signal but names only {skus_here.pop()} — "
                f"a shared pattern must cite at least 2 SKUs that actually share it."
            )

    return errors


def portfolio_output_guardrail(
    output: TaskOutput, decisions: list[dict] | None = None
) -> tuple[bool, str]:
    """
    Guardrail for portfolio_synthesis_task.
    Checks:
      1. Output references at least 2 distinct SKU codes — confirms cross-SKU
         analysis rather than a single-SKU description.
      2. When Agent 3's decisions are supplied, every count, SKU and supplier
         claim is fact-checked against them (see check_portfolio_facts).
    """
    sku_refs = set(re.findall(r'\bSKU\d+\b', output.raw))
    if len(sku_refs) < 2:
        return (
            False,
            "Portfolio synthesis must reference at least 2 distinct SKU codes. "
            "Analyse patterns ACROSS all decisions in your context — "
            "do not describe a single SKU in isolation.",
        )

    if decisions:
        errors = check_portfolio_facts(output.raw, decisions)
        if errors:
            return (
                False,
                "Factual errors in portfolio synthesis — fix or remove these claims:\n- "
                + "\n- ".join(errors)
                + "\n" + _ground_truth(decisions),
            )
    return (True, output.raw)
