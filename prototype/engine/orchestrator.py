"""STEP 3 — Evidence Orchestrator.

Retrieves permitted data from simulated systems of record and builds a
Verified Fact State: every fact carries value + source + version + age,
and a freshness verdict (OK / STALE / MISSING / CONFLICTING).
Missing evidence never becomes proof.
"""
from dataclasses import dataclass, field

from .data import sources as S


@dataclass
class FactRecord:
    key: str
    status: str            # OK | STALE | MISSING | CONFLICTING
    system: str
    value: str = ""
    raw: object = None
    ref: str = ""
    version: str = "-"
    age_days: int | None = None
    max_age_days: int | None = None
    conflict_with: str = ""


@dataclass
class VerifiedFactState:
    facts: dict = field(default_factory=dict)

    def add(self, rec: FactRecord):
        self.facts[rec.key] = rec

    def get(self, key) -> FactRecord | None:
        return self.facts.get(key)


def _find_loan_id(query: str, ctx: dict) -> str | None:
    if ctx.get("loan_id"):
        return ctx["loan_id"]
    import re
    m = re.search(r"LN-\d{4}-\d{4,5}", query, re.I)
    if m:
        return m.group(0).upper()
    return None


def _resolve(fact: str, system: str, query: str, ctx: dict):
    """Return (raw, ref, version, age_days) or (None,* ) if absent."""
    if fact == "loan.status":
        lid = _find_loan_id(query, ctx)
        rec = S.LOAN_DB.get(lid) if lid else None
        return (rec["status"], f"LOAN_DB:{lid}", "-", 0) if rec else (None, "", "", None)
    if fact == "loan.disbursed_on":
        lid = _find_loan_id(query, ctx)
        rec = S.LOAN_DB.get(lid) if lid else None
        return (rec["disbursed_on"], f"LOAN_DB:{lid}", "-", 0) if rec else (None, "", "", None)
    if fact == "loan.principal":
        lid = _find_loan_id(query, ctx)
        rec = S.LOAN_DB.get(lid) if lid else None
        return (rec["principal_inr"], f"LOAN_DB:{lid}", "-", 0) if rec else (None, "", "", None)
    if fact == "contract.prepayment":
        lid = _find_loan_id(query, ctx)
        docs = S.CONTRACTS.get(lid) if lid else None
        d = docs[-1] if docs else None
        return (d, d["doc_id"], d["version"], d["age_days"]) if d else (None, "", "", None)
    if fact == "policy.fee_rule":
        p = S.FEE_POLICY
        return (p["rules"], p["policy_id"], p["version"], p["age_days"])
    if fact == "policy.waiver_rule":
        p = S.WAIVER_POLICY
        return (p["rule"], p["policy_id"], p["version"], p["age_days"])
    if fact == "crm.recent_notes":
        lid = _find_loan_id(query, ctx)
        cid = None
        if lid and lid in S.LOAN_DB:
            cid = S.LOAN_DB[lid]["customer_id"]
        elif ctx.get("customer_id"):
            cid = ctx["customer_id"]
        notes = S.CRM_NOTES.get(cid, []) if cid else []
        n = notes[-1] if notes else None
        return (n["text"], n["note_id"], "-", n["age_days"]) if n else (None, "", "", None)
    if fact == "doc.wealth_apr":
        d = S.WEALTH_DOCS[0]
        return (d["value"], d["doc_id"], d["version"], d["age_days"])
    if fact == "offer.bt_eligible":
        offer = S.OFFERS_ENGINE.get("BT-07")
        if offer:
            return (offer["terms"], offer["offer_id"], offer["version"], offer["age_days"])
        return (None, "", "", None)
    if fact == "kb.entry":
        kb = S.PRODUCT_KB.get(ctx.get("kb_key", "branch_hours"))
        return (kb["value"], kb["ref"], "-", kb["age_days"]) if kb else (None, "", "", None)
    return (None, "", "", None)


def fetch(requirements, query: str, ctx: dict) -> VerifiedFactState:
    state = VerifiedFactState()
    for req in requirements:
        raw, ref, version, age = _resolve(req.fact, req.system, query, ctx)
        if raw is None:
            rec = FactRecord(req.fact, "MISSING", req.system, max_age_days=req.max_age_days)
        elif req.max_age_days is not None and (age or 0) > req.max_age_days:
            rec = FactRecord(req.fact, "STALE", req.system, value=str(raw)[:90], raw=raw,
                             ref=ref, version=version, age_days=age, max_age_days=req.max_age_days)
        else:
            rec = FactRecord(req.fact, "OK", req.system, value=str(raw)[:90], raw=raw,
                             ref=ref, version=version, age_days=age, max_age_days=req.max_age_days)
        state.add(rec)

    # Conflict detection: policy says waivers need approval, CRM note claims auto-waiver.
    pol = state.get("policy.waiver_rule")
    note = state.get("crm.recent_notes")
    if pol and note and pol.status == "OK" and note.status == "OK" \
            and "automatic" in note.raw.lower():
        note.status = "CONFLICTING"
        note.conflict_with = f"{pol.ref} {pol.version}"
        note.value += " ⚠ contradicts policy"
    return state
