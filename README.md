# ControlPlane.ai - Accenture Innovation Challenge Round 2

ControlPlane.ai is an evidence-enforcement layer for enterprise AI. It verifies high-impact claims against governed sources before an answer reaches the user.

> Evidence before answer. No proof, no confident claim.

## Submission contents

```text
ControlPlane_Round2/
|- accenture_round2.pdf
|- NewGenLabs_ControlPlane.ai.pdf
|- 01_Business_Proposal/
|  |- ControlPlane.ai_Business_Proposal.md
|- 02_Pitch_Deck/
|  |- ControlPlane.ai_Round2_Pitch.pptx
|- output/pdf/
|  |- ControlPlane.ai_Business_Proposal.pdf
|- prototype/
|  |- app.py
|  |- engine/
|  |- tests/
|  |- requirements.txt
|  |- run.sh
```

## Run the prototype

```bash
cd prototype
./run.sh
```

The app opens at `http://localhost:8501`.

Manual setup:

```bash
cd prototype
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/streamlit run app.py
```

## What is implemented

```text
Risk Router -> Fact Contract -> Evidence Orchestrator -> Typed Verifiers
-> Decision Gate -> Wording Guard -> Answer + Evidence Receipt
```

The prototype includes:

- Three use-case profiles with separate strictness settings and latency budgets.
- Versioned YAML fact contracts with required systems, freshness limits, and criticality.
- Signed-contract precedence over generic policy. A policy cannot shorten a customer's contractual lock-in.
- Parallel database, calculator, rules, and local similarity checks.
- Seven decisions: `ALLOW`, `EDIT`, `BLOCK`, `ABSTAIN`, `ESCALATE`, `HUMAN_CONFIRM`, and `REDACT`.
- Multi-turn context in the live application, including prior risk decisions and loan identifiers.
- Persistent reviewer calibration stored separately for each intent.
- Full SHA-256 evidence receipts appended to `prototype/logs/receipts.jsonl`.
- GDPR receipt minimisation. EU audit entries do not store plaintext queries.
- Batch recall, false-alarm rate, abstentions, p50 latency, and p95 latency.
- Explicit telemetry for similarity checks, external LLM calls, tokens, and estimated LLM cost. The current prototype reports zero external LLM use.

## Demo scenarios

| Scenario | Expected result |
|---|---|
| Branch hours or current home-loan rate | `ALLOW` through the verified fast path |
| Foreclosure with an incorrect zero-fee assumption | `EDIT` with the calculated fee |
| Foreclosure inside the signed lock-in | `BLOCK` with contract evidence |
| Unknown loan product | `ABSTAIN` |
| CRM waiver promise conflicting with policy | `ESCALATE` |
| Stale wealth APR source | `ESCALATE` |
| Third-party personal-data request | `REDACT` |
| Irreversible transfer instruction | `HUMAN_CONFIRM` |
| Verified balance-transfer eligibility | `ALLOW` with the offer rate and checked criteria |

## Validation

Run all checks from the repository root:

```bash
prototype/.venv/bin/python -m unittest prototype.tests.test_submission_readiness
prototype/.venv/bin/python prototype/tests/smoke.py
prototype/.venv/bin/python prototype/tests/run_suite.py
prototype/.venv/bin/python -m compileall -q prototype
```

The exact submission-readiness tests cover contract authority, live multi-turn context, per-intent feedback persistence, GDPR-safe audit logging, receipt integrity, truthful model telemetry, latency percentiles, and useful customer-facing answers.

All data is simulated. The prototype makes no external network or model calls.
