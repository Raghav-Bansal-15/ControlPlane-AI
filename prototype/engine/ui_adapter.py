"""Public adapter between the Streamlit session and the decision pipeline."""

import re

from .pipeline import run_pipeline


LOAN_ID_RE = re.compile(r"\bLN-\d{4}-\d{4,5}\b", re.I)


def _latest_loan_id(query: str, history: list[dict]) -> str | None:
    current = LOAN_ID_RE.search(query)
    if current:
        return current.group(0).upper()
    for item in reversed(history):
        saved = item.get("loan_id")
        if saved:
            return str(saved).upper()
        match = LOAN_ID_RE.search(item.get("query") or item.get("q") or "")
        if match:
            return match.group(0).upper()
    return None


def run_user_query(query: str, *, profile_key: str, geo_key: str,
                   threshold: float, history: list[dict],
                   customer_id: str = "CUST-101"):
    """Run one live turn with the governed context accumulated in this session."""
    loan_id = _latest_loan_id(query, history)
    prior = [
        {"intent": item.get("intent"), "action": item.get("action") or item.get("a")}
        for item in history
        if item.get("action") or item.get("a")
    ]
    ctx = {"customer_id": customer_id, "prior": prior}
    if loan_id:
        ctx["loan_id"] = loan_id

    result = run_pipeline(
        query,
        profile_key=profile_key,
        geo_key=geo_key,
        threshold=threshold,
        ctx=ctx,
    )
    entry = {
        "query": query,
        "action": result.decision.action,
        "intent": result.receipt["routing"]["intent"],
        "loan_id": loan_id,
    }
    return result, entry
