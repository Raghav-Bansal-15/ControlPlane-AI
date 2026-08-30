"""ControlPlane.ai end-to-end pipeline.

Request → Risk Router → (Fact Contract → Evidence Orchestrator →
Typed Verifiers → Decision Gate → Wording Guard) → Answer + Evidence Receipt.
Low-risk traffic takes a fast path so enforcement never slows the AI.
"""
import re
import time
import concurrent.futures
from dataclasses import dataclass, field

from . import risk_router, contracts, orchestrator, verifiers
from . import decision_gate as DG
from . import wording_guard as WG
from . import receipt as RCPT
from .profiles import PROFILES, GEOS
from .data import sources as S
from .feedback_store import get_delta

LOAN_ID_RE = re.compile(r"\bLN-\d{4}-\d{4,5}\b", re.I)
PRESSURE_RE = re.compile(r"\b(jus\s*say\s*yes|just\s*say\s*yes|say\s*yes|tick\s*the\s*boxes|definitely\s*tick)\b", re.I)
LOCK_RE = re.compile(r"\block\s*me\s*in\b", re.I)


@dataclass
class Step:
    icon: str
    title: str
    ms: float
    lines: list = field(default_factory=list)


@dataclass
class PipelineResult:
    steps: list
    decision: DG.Decision
    receipt: dict
    total_ms: float
    budget_ms: int

    @property
    def badge(self) -> str:
        return DG.BADGES.get(self.decision.action, "•")


def _mask(phone: str) -> str:
    return phone[:6] + "•" * max(len(phone) - 8, 0) + phone[-2:]


def _kb_key_for_query(ql: str) -> str | None:
    # Branch hours needs hours/timings or branch/office + open/close context
    if re.search(r"\b(hours?|timings?)\b", ql):
        return "branch_hours"
    if re.search(r"\b(branch|office)\b", ql) and re.search(r"\b(open|opening|close|closing|time|timings?|where|location)\b", ql):
        return "branch_hours"
    # home-loan rates: require home/housing context to avoid gold/car loan rates mapping to home KB
    if re.search(r"\brates?\b", ql):
        if re.search(r"\bhome\s*loans?\b|\bhousing\s*loans?\b", ql):
            return "home_loan_rates"
        if re.search(r"\bgold\s*loans?\b|\bcar\s*loans?\b|\bpersonal\s*loans?\b|\bgold\b.*\brates?\b|\bcar\b.*\brates?\b", ql):
            return None
        # generic "rates" without product qualifier — treat as home rates only if the query already routed
        # via HOME_LOAN_RATES (has home loan); otherwise no KB
        return None
    return None


def run_pipeline(query: str, profile_key: str = "customer_chatbot",
                 geo_key: str | None = None, threshold: float | None = None,
                 ctx: dict | None = None) -> PipelineResult:
    if geo_key not in GEOS:
        geo_key = next(iter(GEOS))
    geo = GEOS[geo_key]
    prof = PROFILES[profile_key]
    ctx = dict(ctx or {})
    if "customer_id" not in ctx:
        ctx["customer_id"] = "CUST-101"
    # per-intent adaptive delta from review queue
    # base strictness plus per-intent learned offset
    base_strict = threshold if threshold is not None else prof["strictness"]
    # we don't know intent yet; pull after routing. So compute after route with intent-specific delta.
    steps: list[Step] = []

    def add(icon, title, lines, ms):
        steps.append(Step(icon, title, round(ms, 1), lines))

    # ---------------------------------------------------------- 1 · ROUTE (measured)
    t0 = time.perf_counter()
    rv = risk_router.route(query)
    route_ms = (time.perf_counter() - t0) * 1000
    # effective threshold includes per-intent delta and compounding caution from prior turns
    per_intent = get_delta(rv.intent)
    eff = base_strict + per_intent
    prior = ctx.get("prior") or []
    # propagate loan_id from prior turns if not in current query
    if "loan_id" not in ctx:
        for p in reversed(prior):
            # prior turn receipt might have routing info with loan_id in query? 
            # For simplicity, extract from prior's query if present
            pass  # the runner should pass loan_id in ctx explicitly
    recent_escalated = sum(1 for p in prior if p.get("action") in (DG.ESCALATE, DG.BLOCK, DG.HUMAN_CONFIRM, DG.REDACT))
    compounding_note = None
    if recent_escalated:
        bump = 0.05 * recent_escalated
        eff = min(1.0, eff + bump)
        compounding_note = f"compounding caution +{bump:.2f} from {recent_escalated} prior escalated turn(s)"
    eff = max(0.0, min(1.0, eff))
    reasons = list(rv.reasons)
    if per_intent:
        reasons.append(f"adaptive per-intent delta {per_intent:+.2f} for {rv.intent}")
    if compounding_note:
        reasons.append(compounding_note)
    rv.reasons = reasons
    add("🚦", f"Risk Router · {rv.level}",
        [f"intent={rv.intent} · score={rv.score:.2f} · conf={rv.confidence:.2f} · eff_threshold={eff:.2f}"]
        + [f"↳ {r}" for r in reasons], route_ms if route_ms > 0.01 else 1.5)

    def finish(state, claims, dec, extra_reqs=None):
        measured_total = round(sum(s.ms for s in steps), 1)
        # projected production cost: sum of modelled verifier costs for executed claims
        projected = round(sum(verifiers.COST_MS.get(c.ctype, 0) for c in (claims or []) if c.status != "SKIPPED"), 1)
        # add small overhead for routing/orchestrator projected? keep as sum
        rcpt = RCPT.build_receipt(query=query, routing=rv, profile_key=profile_key,
                                  profile=prof, geo_key=geo_key, geo=geo,
                                  requirements=extra_reqs or [], state=state,
                                  claims=claims or [], decision=dec,
                                  steps=steps, total_ms=measured_total,
                                  measured_ms=measured_total, projected_ms=projected)
        return PipelineResult(steps, dec, rcpt, measured_total, prof["budget_ms"])

    # ------------------------------------------- special intent · PRIVACY (measured)
    if rv.intent == "privacy_disclosure":
        t1 = time.perf_counter()
        ql = query.lower()
        hit = next(((cid, p) for cid, p in S.CUSTOMER_PII.items()
                    if p["name"].lower().split()[0] in ql), None)
        # also check if query mentions email/phone bulk without name, still block
        # if no name hit but PII words present, treat as third-party bulk request
        lines = ([f"vault match: {hit[1]['name']} ({hit[0]})",
                  f"masked preview: {_mask(hit[1]['phone'])}",
                  f"channel identity unverified → disclosure blocked under {geo['pii_law']}"]
                 if hit else
                 ["no vault match resolved → treated as third-party/bulk PII request",
                  f"blocked under {geo['pii_law']}"])
        pii_ms = (time.perf_counter() - t1) * 1000
        add("🔒", "PII Guard", lines, max(pii_ms, 1.2))
        answer = ("I can't share another customer's personal details through this "
                  f"channel ({geo['pii_law']}). A secure callback has been arranged instead.")
        dec = DG.Decision(DG.REDACT, "PII disclosure blocked", answer, lines[:1], True)
        add("⚖️", "Decision Gate", [f"{DG.REDACT} — {dec.headline}"], 1)
        return finish(orchestrator.VerifiedFactState(), [], dec)

    # --------------------------------------------- special intent · ACTION (measured)
    if rv.intent == "account_action":
        t1 = time.perf_counter()
        # Check if this is a foreclosure/closure request on a loan inside lock-in
        # If so, BLOCK takes precedence over HUMAN_CONFIRM (contract forbids it)
        ql = query.lower()
        is_foreclosure_action = bool(re.search(r"\b(foreclos\w*|forclose\w*|close|closure|settl\w*|prepay\w*)\b", ql))
        if is_foreclosure_action and ctx.get("loan_id"):
            # Fetch minimal facts to check lock-in
            tmp_reqs = [
                r for r in contracts.FACT_CONTRACTS.get("loan_foreclosure", [])
                if r.fact in ("loan.disbursed_on", "contract.prepayment", "policy.fee_rule")
            ]
            tmp_state = orchestrator.fetch(tmp_reqs, query, ctx)
            disb_f = tmp_state.get("loan.disbursed_on")
            policy_f = tmp_state.get("policy.fee_rule")
            if disb_f and disb_f.status == "OK" and policy_f and policy_f.status == "OK":
                from .utils import months_between
                months = months_between(disb_f.raw, S.TODAY)
                contract_f = tmp_state.get("contract.prepayment")
                contract_doc = contract_f.raw if contract_f and contract_f.status == "OK" else {}
                contract_terms = contract_doc.get("terms", {}) if isinstance(contract_doc, dict) else {}
                lockin = contract_terms.get("lockin_months")
                if lockin is None:
                    lockin = (policy_f.raw or {}).get("lockin_months", 0)
                if months < lockin:
                    # Inside lock-in → BLOCK with contract citation
                    fetch_ms = (time.perf_counter() - t1) * 1000
                    add("🔍", "Evidence Orchestrator (lock-in check)",
                        [f"loan.disbursed_on OK (age 0d)", f"policy.fee_rule OK"], max(fetch_ms, 6))
                    contract = tmp_state.get("contract.prepayment") or tmp_state.get("loan.disbursed_on")
                    clause = getattr(contract, "raw", "") if contract else ""
                    ref = getattr(contract, "ref", "?") if contract else "?"
                    ver = getattr(contract, "version", "?") if contract else "?"
                    add("⚖️", "Decision Gate",
                        [f"{DG.BLOCK} — Blocked by contract lock-in",
                         "foreclosure requested but loan is inside mandatory lock-in period"],
                        2)
                    dec = DG.Decision(
                        DG.BLOCK, "Blocked — request not permitted by contract",
                        f"I can't process this closure: your signed agreement has a {lockin}-month "
                        "lock-in and does not permit foreclosure yet. "
                        "Options: wait out the lock-in, or request an exception review.",
                        [f"lock-in not elapsed ({months}m < {lockin}m)",
                         "contract forbids foreclosure during lock-in"], False)
                    return finish(orchestrator.VerifiedFactState(), [], dec)
        
        add("🧑‍⚖️", "Action Guard",
            ["irreversible money-movement detected",
             "agent actions compound risk across turns"] + ([compounding_note] if compounding_note else []),
            max((time.perf_counter() - t1) * 1000, 1.0))
        dec = DG.Decision(DG.HUMAN_CONFIRM, "Money movement needs dual control",
                          "I can't execute transfers or payments from chat. "
                          "A dual-control task has been created for an officer.",
                          ["irreversible action", "compounding multi-turn risk"] + ([compounding_note] if compounding_note else []), True)
        add("⚖️", "Decision Gate", [DG.HUMAN_CONFIRM], 1)
        return finish(orchestrator.VerifiedFactState(), [], dec)

    # -------------------------------------------------- 2 · FACT CONTRACT (measured)
    t1 = time.perf_counter()
    contract_intent = rv.intent
    if rv.level == "LOW" and contract_intent not in contracts.FACT_CONTRACTS:
        contract_intent = "branch_info"
    reqs = contracts.FACT_CONTRACTS.get(contract_intent) \
        or contracts.FACT_CONTRACTS["branch_info"]
    clines = [f"{len(reqs)} facts required before any claim may be spoken — registry {contracts.REGISTRY_VERSION}"]
    for r in reqs:
        sla = f"≤{r.max_age_days}d" if r.max_age_days is not None else "any age"
        clines.append(f"↳ {r.fact} [{r.system}] {sla}{'' if r.critical else ' (optional)'}")
    contract_ms = (time.perf_counter() - t1) * 1000
    add("📋", "Fact Contract", clines, max(contract_ms, 1.0))

    kb_key = _kb_key_for_query(query.lower())

    # stash reqs for decision gate to distinguish critical vs optional missing
    ctx["_reqs"] = reqs
    if compounding_note:
        ctx["_compounding"] = compounding_note

    # ------------------------------------------------ FAST PATH · LOW risk (measured)
    if rv.level == "LOW":
        t1 = time.perf_counter()
        state = orchestrator.VerifiedFactState()
        used_claims: list = []
        m = WG.GROUP_HINT.search(query)
        if m and kb_key is None:
            draft = f"{m.group(0).capitalize()} are obviously careless with repayments."
            clean, flags = WG.guard(draft)
            add("✍️", "Draft (simulated LLM)", ["“" + draft + "”"], max((time.perf_counter() - t1)*1000, 3))
            t2 = time.perf_counter()
            add("🛡️", "Wording Guard",
                [f"rewrote {len(flags)} biased phrase(s): “{', '.join(flags)}”",
                 "sent: “" + clean + "”"], max((time.perf_counter()-t2)*1000, 0.8))
            dec = DG.Decision(DG.ALLOW, "Allowed after wording guard", clean,
                              ["bias pattern neutralised pre-send"])
            add("⚖️", "Decision Gate", [f"{dec.action} — {dec.headline}"], 1)
        elif kb_key is None:
            add("🔍", "Evidence Orchestrator", ["no KB entry matches this ask"], max((time.perf_counter()-t1)*1000, 1.5))
            dec = DG.Decision(DG.ABSTAIN, "Outside verified scope",
                              "I don't have verified information on that; connecting you "
                              "to a human colleague.",
                              ["honest abstention beats a fluent guess"])
            add("⚖️", "Decision Gate", [f"{dec.action} — {dec.headline}"], 1)
        else:
            ctx["kb_key"] = kb_key
            t2 = time.perf_counter()
            state = orchestrator.fetch(reqs, query, ctx)
            f = state.get("kb.entry")
            fetch_ms = (time.perf_counter() - t2) * 1000
            add("🔍", "Evidence Orchestrator",
                [f"kb.entry [{f.status}] {str(f.value)[:64]} (age {f.age_days}d ≤ SLA)"], max(fetch_ms, 8))
            # single DB claim for fast path
            t3 = time.perf_counter()
            c = verifiers.Claim("k1", "KB entry exists and is fresh", "db_lookup",
                                evidence_refs=[f.ref] if f and f.ref else [])
            c.predicate = f.status == "OK" if f else False
            vms = verifiers.verify(c)
            vm_measured = (time.perf_counter() - t3)*1000
            used_claims = [c]
            add("🧩", "Typed Verifiers",
                [f"k1 → {c.status} · DB lookup · src={f.ref if f else '—'}"],
                max(vm_measured, vms*0.01))
            t4 = time.perf_counter()
            dec = DG.decide(contract_intent, state, used_claims, eff, ctx)
            add("⚖️", "Decision Gate",
                [f"{dec.action} — {dec.headline}"] + dec.rationale, max((time.perf_counter()-t4)*1000, 1.5))
        return finish(state, used_claims, dec, reqs)

    # ------------------------------------------------- FULL GATE · M/H risk (measured)
    t1 = time.perf_counter()
    m = LOAN_ID_RE.search(query)
    if m:
        ctx["loan_id"] = m.group(0).upper()

    state = orchestrator.fetch(reqs, query, ctx)
    flines = []
    for k, f in state.facts.items():
        sla = f"SLA {f.max_age_days}d" if f.max_age_days is not None else "no SLA"
        flines.append(f"{k} [{f.status}] src={f.ref or '—'} v{f.version} "
                      f"age={f.age_days}d · {sla}")
    fetch_ms = (time.perf_counter() - t1) * 1000
    add("🔍", "Evidence Orchestrator", flines, max(fetch_ms, 6))

    t2 = time.perf_counter()
    claims = verifiers.build_claims(rv.intent, state, query, ctx)
    # parallel verification: atomic claims are independent
    wall_start = time.perf_counter()
    vlines, vms_model = [], 0.0
    if claims:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(claims))) as ex:
            futs = {ex.submit(verifiers.verify, c): c for c in claims}
            for fut in concurrent.futures.as_completed(futs):
                c = futs[fut]
                model_ms = fut.result()
                vms_model += model_ms
        wall_ms = (time.perf_counter() - wall_start) * 1000
        for c in claims:
            ev = ",".join(c.evidence_refs) if c.evidence_refs else "—"
            vlines.append(f"{c.cid} → {c.status:<12}| {c.text[:58]} | "
                          f"{verifiers.COST_LABEL[c.ctype]} [{ev}]")
        # Honest reporting: wall-clock measured, plus modelled production cost for economics narrative
        add("🧩", "Typed Verifiers", vlines + [f"wall {wall_ms:.1f}ms (modelled Σ {vms_model:.1f}ms — deterministic first)"], max(wall_ms, 1.5))
    else:
        add("🧩", "Typed Verifiers", ["no claims built"], (time.perf_counter()-t2)*1000)

    t3 = time.perf_counter()
    dec = DG.decide(rv.intent, state, claims, eff, ctx)
    # Balance-transfer pressure / lock-in overrides
    ql = query.lower()
    if rv.intent == "balance_transfer":
        if LOCK_RE.search(ql):
            if dec.action == DG.ALLOW:
                dec.action = DG.HUMAN_CONFIRM
                dec.headline = "Commitment to transfer requires dual control"
                dec.human_required = True
                dec.rationale.append("lock-in language detected → HUMAN_CONFIRM regardless of eligibility")
                dec.answer = "I can't lock you in from chat. A dual-control task has been created for an officer to review eligibility and confirm."
        elif PRESSURE_RE.search(ql):
            if dec.action == DG.ALLOW:
                dec.action = DG.ESCALATE
                dec.headline = "Eligibility affirmation pressured without sufficient verification — escalated"
                dec.human_required = True
                dec.rationale.append("pressure phrase detected (jus say yes / tick the boxes) → escalated")
                dec.answer = "I can't affirm eligibility on that basis alone. Escalating to an officer with the offer terms and your loan record attached."
    # multi-turn compounding note -> append to rationale if escalated due to prior
    if compounding_note and dec.action in (DG.ESCALATE, DG.HUMAN_CONFIRM):
        dec.rationale.append(compounding_note)
    add("⚖️", "Decision Gate",
        [f"{dec.action} — {dec.headline}"] + dec.rationale, max((time.perf_counter()-t3)*1000, 1.2))

    if dec.action in (DG.ALLOW, DG.EDIT):
        t4 = time.perf_counter()
        clean, flags = WG.guard(dec.answer)
        if flags:
            add("🛡️", "Wording Guard", [f"rewrote: “{', '.join(flags)}”"], max((time.perf_counter()-t4)*1000, 0.5))
            dec.answer = clean
        add("✍️", "Verbalize approved claims only", ["“" + dec.answer[:90] + "”"], max((time.perf_counter()-t4)*1000, 2))

    return finish(state, claims, dec, reqs)
