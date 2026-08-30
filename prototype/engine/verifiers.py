"""STEP 4 — Typed Claim Verifiers.

Answers are drafted as atomic claims and each is routed to a suitable local
verifier: database lookup, calculator, rules engine, or similarity scorer.
The prototype makes no external model calls.
"""
import re
from dataclasses import dataclass, field

from .data import sources as S
from .utils import months_between, inr

PROVEN, CONTRADICTED = "PROVEN", "CONTRADICTED"
INSUFFICIENT, CONFLICTING, STALE, SKIPPED = "INSUFFICIENT", "CONFLICTING", "STALE", "SKIPPED"

COST_MS = {"db_lookup": 1.2, "calculator": 2.1, "rules_engine": 4.0, "similarity": 3.0}
COST_LABEL = {"db_lookup": "DB check", "calculator": "Calculator",
              "rules_engine": "Rules engine", "similarity": "Local similarity scorer"}


@dataclass
class Claim:
    cid: str
    text: str
    ctype: str                 # db_lookup | calculator | rules_engine | similarity
    status: str = "PENDING"
    verifier_note: str = ""
    evidence_refs: list = field(default_factory=list)
    confidence: float = 0.0
    severity: str = "hard"     # hard -> BLOCK path ; soft -> EDIT/correction path


def _db(cid, text, expected, refs) -> Claim:
    c = Claim(cid, text, "db_lookup", evidence_refs=refs)
    c.expected = expected
    return c


def _jaccard(a: str, b: str) -> float:
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def build_claims(intent: str, state, query: str, ctx: dict) -> list[Claim]:
    claims: list[Claim] = []

    if intent == "loan_foreclosure":
        lid = ctx.get("loan_id") or "-"
        status_f, disb_f = state.get("loan.status"), state.get("loan.disbursed_on")
        if not (status_f and disb_f and status_f.status == "OK"):
            return [Claim("c0", "Loan record exists in system of record", "db_lookup",
                          INSUFFICIENT, "no authoritative loan row found", confidence=0.0)]

        loan_raw = S.LOAN_DB.get(ctx["loan_id"], {})
        principal_f = state.get("loan.principal")
        contract_f, policy_f = state.get("contract.prepayment"), state.get("policy.fee_rule")

        claims.append(_db(
            "c1", f"Loan {lid} is an active {loan_raw.get('product','?').replace('_',' ')}",
            "active", [status_f.ref]))
        claims[-1].predicate = (status_f.raw == "active")

        months = months_between(disb_f.raw, S.TODAY)
        contract_doc = contract_f.raw if contract_f and contract_f.status == "OK" else {}
        contract_terms = contract_doc.get("terms", {}) if isinstance(contract_doc, dict) else {}
        policy_terms = policy_f.raw if policy_f and policy_f.status == "OK" else {}
        lockin = contract_terms.get("lockin_months")
        lockin_source = contract_f.ref if lockin is not None and contract_f else None
        if lockin is None:
            lockin = policy_terms.get("lockin_months", 0)
            lockin_source = policy_f.ref if policy_f else None
        claims.append(Claim(
            "c2", f"Mandatory lock-in of {lockin} months has elapsed "
                  f"(disbursed {disb_f.raw} → {months}m ago)",
            "rules_engine", evidence_refs=[ref for ref in (lockin_source, disb_f.ref) if ref]))
        claims[-1].predicate = (months >= lockin)
        claims[-1].lockin_months = lockin
        claims[-1].elapsed_months = months
        claims[-1].contract_ref = contract_f.ref if contract_f else ""

        if months >= lockin:
            pct_field = "fee_pct_within_24m" if months < 24 else "fee_pct_after_24m"
            pct = contract_terms.get(pct_field)
            fee_ref = contract_f.ref if pct is not None and contract_f else None
            if pct is None:
                pct = policy_terms.get(pct_field)
                fee_ref = policy_f.ref if policy_f else None
            principal = principal_f.raw if principal_f and principal_f.status == "OK" else None
            if pct is not None and principal:
                fee = round((pct / 100.0) * principal)
                claims.append(Claim(
                    "c3", f"Applicable foreclosure fee = {pct}% of {inr(principal)} = {inr(fee)}",
                    "calculator", evidence_refs=[ref for ref in (fee_ref, principal_f.ref) if ref]))
                claims[-1].fee = fee
                claims[-1].predicate = True   # proven by construction once computed
            else:
                for i in ("c3", "c4"):
                    claims.append(Claim(i, "(skipped — fee schedule incomplete)", "calculator",
                                        SKIPPED, "policy/principal unavailable"))
                return claims
            # broader asks_free detection: without penalty/fee, freely, at zero cost etc
            ql = query.lower()
            # only trigger on DECLARATIVE assertions, not questions
            # questions like "is my fee zero?" "is it zero?" should NOT trigger EDIT
            # they should ALLOW with fee disclosed
            asks_free = any(w in ql for w in
                            ["without penalty", "no penalty", "without charge", "no charge",
                             "without fee", "no fee", "free of", "zero charges", "zero cost",
                             "without penalty", "at zero", "with zero", "owe nothing",
                             "no charges"])
            # removed: "is zero" (question form), "zero" alone (too broad)
            # only create the "customer assumes zero penalty" claim when they actually assert it
            if asks_free:
                claims.append(Claim(
                    "c4", "Customer assumption “closure carries no penalty” holds",
                    "rules_engine", severity="soft",
                    evidence_refs=[contract_f.ref] if contract_f else []))
                claims[-1].predicate = (fee == 0)
                claims[-1].assumption_checked = True
        else:
            for i in ("c3", "c4"):
                claims.append(Claim(i, "(skipped — line still inside lock-in)", "db_lookup",
                                    SKIPPED, "not reachable while feasibility claim fails"))

    elif intent == "fee_waiver":
        pol_f, note_f = state.get("policy.waiver_rule"), state.get("crm.recent_notes")
        claims.append(Claim(
            "c1", "Waivers require documented Branch Manager approval",
            "rules_engine", evidence_refs=[pol_f.ref] if pol_f else []))
        claims[-1].predicate = bool(pol_f and pol_f.status == "OK")
        if note_f:
            claims.append(Claim(
                "c2", "A prior agent's promise of an automatic waiver exists in CRM",
                "db_lookup", evidence_refs=[note_f.ref]))
            claims[-1].predicate = True          # existence proven → conflict stands
        if state.get("crm.recent_notes") and state.get("crm.recent_notes").status == "CONFLICTING":
            claims.append(Claim("c3", "CRM promise conflicts with current policy",
                                "rules_engine", CONFLICTING,
                                "policy vs CRM note — human arbitration required"))

    elif intent == "wealth_rate_query":
        doc_f = state.get("doc.wealth_apr")
        c = Claim("c1", "APR schedule is fresh enough to quote from",
                  "db_lookup", evidence_refs=[doc_f.ref] if doc_f else [])
        c.predicate = doc_f is not None and doc_f.status == "OK"
        claims.append(c)

    elif intent == "balance_transfer":
        status_f, policy_f, offer_f = (state.get("loan.status"),
                                       state.get("policy.fee_rule"),
                                       state.get("offer.bt_eligible"))
        c1 = _db("c1", "Loan is active and transfer-eligible on status",
                 "active", [status_f.ref] if status_f else [])
        c1.predicate = bool(status_f and status_f.status == "OK" and status_f.raw == "active")
        claims.append(c1)

        c2 = Claim("c2", "No exit/foreclosure charge blocks the switch",
                   "rules_engine",
                   evidence_refs=[policy_f.ref] if policy_f else [])
        rules = getattr(policy_f, "raw", None) or {}
        c2.predicate = policy_f is not None and policy_f.status == "OK" \
            and rules.get("fee_pct_after_24m") == 0.0
        claims.append(c2)

        # Fuzzy offer-to-profile matching — real local similarity, not stub truthiness
        c3 = Claim("c3", "Competing-offer terms match borrower profile (fuzzy match)",
                   "similarity",
                   evidence_refs=[offer_f.ref] if offer_f else [])
        if offer_f and offer_f.status == "OK" and offer_f.raw:
            offer = S.OFFERS_ENGINE.get("BT-07", {})
            terms = offer.get("terms", {}) if offer else {}
            loan_raw = S.LOAN_DB.get(ctx.get("loan_id", ""), {}) if ctx.get("loan_id") else {}
            # if no loan_id but we have loan context via generic (unlikely), treat as missing
            if loan_raw:
                prod_match = 1.0 if loan_raw.get("product") in terms.get("eligible_products", []) else 0.0
                region_match = 1.0 if loan_raw.get("region") in terms.get("eligible_regions", []) else 0.0
                outstanding_ok = 1.0 if loan_raw.get("principal_inr", 0) >= terms.get("min_outstanding_inr", 0) else 0.0
                remaining_tenure = loan_raw.get("remaining_tenure_months")
                tenure_match = 1.0 if remaining_tenure is not None \
                    and remaining_tenure >= terms.get("min_remaining_tenure_months", 0) else 0.0
                jacc = _jaccard(offer.get("raw_text", ""), f"{loan_raw.get('product','')} {loan_raw.get('region','')}")
                weighted = 0.35 * prod_match + 0.20 * region_match + 0.20 * outstanding_ok + 0.15 * tenure_match + 0.10 * jacc
                c3.predicate = weighted >= 0.65
                c3.verifier_extra = (
                    f"similarity={weighted:.2f} ≥0.65 (product {prod_match:.0f}, "
                    f"region {region_match:.0f}, tenure {tenure_match:.0f}, jacc {jacc:.2f})"
                )
                c3.raw_similarity = weighted
            else:
                # no loan_id → cannot fuzzy match profile → insufficient
                c3.predicate = False
                c3.verifier_extra = "no loan profile for fuzzy match"
        else:
            c3.predicate = False
            c3.verifier_extra = "offer unavailable"
        claims.append(c3)

    elif intent == "recommendation":
        claims.append(Claim("c1", "Suitability policy available for advice disclaimer",
                            "db_lookup", INSUFFICIENT,
                            "suitability policy not provisioned in this sim",
                            confidence=0.5))

    elif intent == "unknown_financial":
        claims.append(Claim("c0", "Query maps to a governed financial intent", "rules_engine",
                            INSUFFICIENT, "unclassified intent — no contract, abstain by design"))

    return claims


def verify(claim: Claim):
    """Mutates claim with status/confidence/note. Returns ms spent."""
    ms = COST_MS[claim.ctype]
    if claim.status in (SKIPPED, CONFLICTING):
        return ms * 0.1
    ok = getattr(claim, "predicate", False)
    if claim.status == "PENDING":
        claim.status = PROVEN if ok else CONTRADICTED
        # keep INSUFFICIENT as is (already set)
        if claim.status == "PENDING" and hasattr(claim, "predicate"):
            # INSUFFICIENT claims stay INSUFFICIENT unless predicate overrides? Already handled via explicit status in builder
            pass
    # handle explicitly created INSUFFICIENT claims (c0 for unknown/missing)
    if claim.status == INSUFFICIENT:
        ms_eff = ms * 0.1
        base = 0.6
        claim.confidence = base
        src = ", ".join(claim.evidence_refs) if claim.evidence_refs else "—"
        extra = getattr(claim, "verifier_extra", "")
        claim.verifier_note = f"{COST_LABEL[claim.ctype]} · src={src}{(' · ' + extra) if extra else ''}"
        return ms_eff
    base = {PROVEN: 0.97, CONTRADICTED: 0.95, INSUFFICIENT: 0.6}.get(claim.status, 0.5)
    # fuzzy similarity matches are less certain than deterministic checks
    claim.confidence = 0.78 if (claim.status == PROVEN and claim.ctype == "similarity") else base
    src = ", ".join(claim.evidence_refs) if claim.evidence_refs else "—"
    extra = getattr(claim, "verifier_extra", "")
    claim.verifier_note = f"{COST_LABEL[claim.ctype]} · src={src}{(' · ' + extra) if extra else ''}"
    return ms
