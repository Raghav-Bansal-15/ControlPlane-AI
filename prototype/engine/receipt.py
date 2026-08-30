"""STEP 6 — Evidence Receipt.

Every answer ships with replayable proof: what was checked, against
which source/version, what passed, what was withheld, latency spent,
and a jurisdiction block. sha256 integrity over the canonical body.
"""
import datetime
import hashlib
import json

from .verifiers import COST_MS, COST_LABEL
try:
    from .contracts import REGISTRY_VERSION
except Exception:
    REGISTRY_VERSION = "unknown"


def build_receipt(*, query, routing, profile_key, profile, geo_key, geo,
                  requirements, state, claims, decision, steps, total_ms, measured_ms=None, projected_ms=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    is_eu = geo.get("code") == "EU"
    # GDPR minimisation: omit plaintext query in receipt body
    if is_eu:
        request_block = {
            "query_sha1_12": hashlib.sha1(query.encode()).hexdigest()[:12],
            "plaintext_stored": False,
            "note": "plaintext minimised per GDPR",
        }
    else:
        request_block = {
            "query": query,
            "query_sha1_12": hashlib.sha1(query.encode()).hexdigest()[:12],
            "plaintext_stored": True,
        }
    body = {
        "receipt_version": "1.0",
        "generated_at": now.isoformat(timespec="seconds"),
        "contract_registry_version": REGISTRY_VERSION,
        "jurisdiction": {
            "code": geo["code"],
            "pii_law": geo["pii_law"],
            "credit_rule": geo["credit_rule"],
            "retention_note": geo["receipt_note"],
        },
        "use_case": {
            "profile": profile_key,
            "latency_budget_ms": profile["budget_ms"],
            "configured_strictness": profile["strictness"],
        },
        "request": request_block,
        "routing": {
            "risk_level": routing.level,
            "score": round(routing.score, 2),
            "intent": routing.intent,
            "reasons": routing.reasons,
        },
        "fact_contract": [
            {"fact": r.fact, "system": r.system,
             "max_age_days": r.max_age_days, "critical": r.critical}
            for r in (requirements or [])
        ],
        "verified_fact_state": [
            {"key": f.key, "status": f.status, "system": f.system, "ref": f.ref,
             "version": f.version, "age_days": f.age_days, "sla_days": f.max_age_days,
             "conflict_with": f.conflict_with}
            for f in state.facts.values()
        ],
        "claims": [
            {"id": c.cid, "text": c.text, "verifier": COST_LABEL.get(c.ctype, c.ctype),
             "status": c.status, "confidence": round(c.confidence, 2),
             "severity": c.severity, "evidence": c.evidence_refs}
            for c in claims
        ],
        "decision": {
            "action": decision.action,
            "headline": decision.headline,
            "human_required": decision.human_required,
            "rationale": decision.rationale,
            "answer": decision.answer,
        },
        "performance": {},
    }

    breakdown = {}
    for c in claims:
        label = COST_LABEL.get(c.ctype, c.ctype)
        if c.status != "SKIPPED":
            breakdown[label] = round(breakdown.get(label, 0) + COST_MS[c.ctype], 1)
    # measured is real wall-clock; projected is model cost for prod economics story
    m = measured_ms if measured_ms is not None else total_ms
    proj = projected_ms if projected_ms is not None else round(sum(COST_MS.get(c.ctype, 0) for c in claims if c.status != "SKIPPED"), 1)
    body["performance"] = {
        "measured_ms": round(m, 1),
        "projected_production_ms": round(proj, 1),
        "total_ms": round(m, 1),
        "budget_ms": profile["budget_ms"],
        "budget_utilization_pct": round(100 * m / profile["budget_ms"], 1) if profile["budget_ms"] else 0,
        "verifier_cost_breakdown_ms": breakdown,
        "semantic_similarity_checks": sum(1 for c in claims if c.ctype == "similarity"),
        "llm_judge_calls": 0,
        "llm_tokens": 0,
        "estimated_llm_cost_usd": 0.0,
        "note": "All prototype checks are local. No external model call, token use, or LLM cost is claimed.",
    }

    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()
    body["integrity_sha256"] = digest
    return body
