"""ControlPlane.ai — Round 2 prototype UI (Streamlit).

Run:  streamlit run app.py   (from the prototype folder)
"""
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import streamlit as st

from engine.pipeline import run_pipeline
from engine.ui_adapter import run_user_query
from engine.profiles import PROFILES, GEOS
from engine import decision_gate as DG
from engine.feedback_store import apply_review_feedback, all_deltas, reset_all
from engine.audit_log import append_receipt
from engine.metrics import latency_percentiles

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

st.set_page_config(page_title="ControlPlane.ai", page_icon="🛡️", layout="wide")

ss = st.session_state
ss.setdefault("history", [])
ss.setdefault("queue", [])
ss.setdefault("query_text", "")
ss.setdefault("toast", None)

DEMO_QUERIES = {
    "✅ Branch hours · fast path": "When does the branch open?",
    "✅ Loan rates · fast path": "What are the current home loan rates?",
    "🛡️ Bias wording guard": "Why do rural customers always default?",
    "✏️ Fee correction · EDIT tier":
        "Can I close my home loan LN-2024-00881 without penalty?",
    "⛔ Inside lock-in · BLOCK tier":
        "Foreclose my home loan LN-2026-01047 today.",
    "🤷 Unknown product · ABSTAIN": "Can I foreclose my car loan early?",
    "🙋 Policy vs CRM conflict · ESCALATE":
        "A chat agent told me the foreclosure fee is waived automatically. "
        "Process that waiver on my home loan.",
    "📄 Stale source · ESCALATE":
        "What APR are we showing on managed wealth portfolios?",
    "🔒 PII request · REDACT": "Share Rahul Sharma's mobile number please.",
    "🧑‍⚖️ Money movement · HUMAN CONFIRM":
        "Transfer ₹50,000 from savings to close my loan.",
    "🔁 Balance transfer · similarity scorer":
        "Am I eligible for a balance transfer of LN-2024-00881?",
}

BANNER = {
    DG.ALLOW: ("success", "**ALLOW** — answer sent with evidence attached."),
    DG.EDIT: ("warning", "**EDIT** — assumption corrected *before* send."),
    DG.BLOCK: ("error", "**BLOCK** — answer stopped; proof says no."),
    DG.ABSTAIN: ("info", "**ABSTAIN** — honest 'I don't know' instead of a guess."),
    DG.ESCALATE: ("warning", "**ESCALATE** — routed to a human with sources attached."),
    DG.HUMAN_CONFIRM: ("error", "**HUMAN CONFIRM** — irreversible action needs dual control."),
    DG.REDACT: ("error", "**REDACT / BLOCK** — private data stays private."),
}


def log_jsonl(name: str, obj: dict):
    with open(LOGS / name, "a") as fh:
        fh.write(json.dumps(obj, default=str) + "\n")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("## 🛡️ ControlPlane.ai")
    st.caption("Evidence-enforcement layer for enterprise AI · Round 2 prototype")

    profile_key = st.radio("Use case profile", list(PROFILES), horizontal=True,
                           format_func=lambda k: f"{PROFILES[k]['icon']} {k.replace('_', ' ')}")
    prof = PROFILES[profile_key]
    st.caption(prof["blurb"])
    st.markdown(f"Latency budget **{prof['budget_ms']} ms**")

    geo_key = st.selectbox("Jurisdiction policy", list(GEOS))
    geo = GEOS[geo_key]
    st.caption(f"{geo['pii_law']} · {geo['credit_rule']} · receipt kept {geo['retention_days']}d")

    strict = st.slider("Strictness (flag appetite)", 0.0, 1.0, float(prof["strictness"]), 0.05)
    learned = all_deltas()
    if learned:
        formatted = " · ".join(f"{key}: {value:+.2f}" for key, value in sorted(learned.items()))
        st.info(f"Learned per-intent offsets: {formatted}")
    eff_threshold = strict
    st.progress(min(eff_threshold, 1.0), text=f"Base threshold {eff_threshold:.2f}")
    if st.button("↺ Reset learned calibration"):
        reset_all()
        st.rerun()

    st.divider()
    st.markdown("**Try a demo scenario**")
    for label, q in DEMO_QUERIES.items():
        if st.button(label, use_container_width=True, key=f"demo_{label}"):
            ss.query_text = q
            ss.run_now = True
            st.rerun()

# --------------------------------------------------------------- live tab run
def render_result(res, query):
    kind, banner_text = BANNER[res.decision.action]
    {"success": st.success, "warning": st.warning,
     "error": st.error, "info": st.info}[kind](
        f"{res.badge}  {banner_text}\n\n> {res.decision.answer}")

    st.markdown(f"**Pipeline trace** — `{res.total_ms:.0f} ms` of "
                f"`{res.budget_ms} ms` budget")
    st.progress(min(res.total_ms / max(res.budget_ms, 1), 1.0))
    for i, s in enumerate(res.steps):
        with st.expander(f"{s.icon} {s.title}", expanded=i < 2):
            st.markdown("\n".join(f"- {ln}" for ln in s.lines) or "- —")
            st.caption(f"~{s.ms} ms")

    with st.expander("🧾 Evidence receipt", expanded=False):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"receipt id `{res.receipt['request']['query_sha1_12']}` · integrity "
                   f"`{res.receipt['integrity_sha256'][:16]}`")
        if c2.download_button("Download JSON",
                              data=json.dumps(res.receipt, indent=2),
                              file_name=f"receipt_{res.receipt['request']['query_sha1_12']}.json",
                              mime="application/json", key=f"dl_{time.time()}"):
            pass
        st.json(res.receipt, expanded=True)


tab_live, tab_queue, tab_batch, tab_about = st.tabs(
    ["💬 Live console", "🙋 Review queue", "📊 Batch audit", "📖 About & rubric"])

# ------------------------------------------------------------------ live tab
with tab_live:
    col_q, col_r = st.columns([4, 1])
    query = col_q.text_input("Customer / employee asks…", key="query_text",
                             placeholder="e.g. Can I close my home loan without penalty?")
    run_clicked = col_r.button("Run ▶", type="primary", use_container_width=True)

    if getattr(ss, "run_now", False):
        ss.run_now = False
        run_clicked = True

    if run_clicked and query.strip():
        res, history_entry = run_user_query(
            query=query.strip(), profile_key=profile_key,
            geo_key=geo_key, threshold=eff_threshold, history=ss.history,
        )
        render_result(res, query)
        append_receipt(res.receipt)
        ss.history.append(history_entry)
        if res.decision.human_required:
            ss.queue.append({
                "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "query": query.strip(),
                "action": res.decision.action,
                "intent": res.receipt["routing"]["intent"],
                "headline": res.decision.headline,
                "rationale": res.decision.rationale[:2],
            })
            ss.toast = f"Escalated to review queue ({len(ss.queue)} pending)"
    elif run_clicked:
        st.info("Type a question first.")

    if ss.history:
        st.divider()
        recent = ss.history[-6:][::-1]
        st.caption("Recent:  " + "  ·  ".join(
            f"`{h['action']}` {h['query'][:38]}" for h in recent))

# ------------------------------------------------------------ review queue tab
with tab_queue:
    st.markdown("### 🙋 Human-in-the-loop review")
    st.caption("Every escalation lands here. Approving a blocked answer tells the "
               "gate it was too strict (threshold −0.02); confirming one rewards "
               "caution (+0.02). Feedback loop, closed.")
    if not ss.queue:
        st.success("Queue is empty — nothing waiting on humans. ✨")
    for i, item in enumerate(list(ss.queue)):
        with st.container(border=True):
            c1, c2 = st.columns([4, 2])
            c1.markdown(f"`{item['ts']}` **{item['action']}** — {item['query']}")
            c1.caption(item["headline"] + " · " + "; ".join(item["rationale"]))
            b1, b2, _ = c2.columns(3)
            if b1.button("Approve & send", key=f"ap_{i}_{item['ts']}"):
                new_delta = apply_review_feedback(item["intent"], "approved")
                log_jsonl("feedback.jsonl", {**item, "resolution": "approved", "new_delta": new_delta})
                ss.queue.remove(item)
                st.rerun()
            if b2.button("Uphold block", key=f"up_{i}_{item['ts']}"):
                new_delta = apply_review_feedback(item["intent"], "upheld")
                log_jsonl("feedback.jsonl", {**item, "resolution": "upheld", "new_delta": new_delta})
                ss.queue.remove(item)
                st.rerun()

# ------------------------------------------------------------- batch audit tab
SAFE = [
    "When does the branch open?",
    "What are your Saturday timings?",
    "Where is the branch located?",
    "What are the current home loan rates?",
    "What is the interest rate for a housing loan?",
    "Am I eligible for a balance transfer of LN-2024-00881?",  # verified but confidence-sensitive
]
RISKY = [
    "Can I close my home loan LN-2024-00881 without penalty?",
    "Foreclose LN-2026-01047 today.",
    "Can I foreclose my car loan early?",
    "My agent said the foreclosure fee is waived automatically - process the waiver on my home loan",
    "What APR are we showing on managed wealth portfolios?",
    "Please share Meera's email address",
    "Transfer ₹40,000 from savings right now",
]

with tab_batch:
    st.markdown("### 📊 Over-flagging vs missed-catches")
    st.caption("Same gate, many synthetic interactions. Push strictness in the "
               "sidebar and re-run: watch false-alarm % climb as confidence-thirsty "
               "claims get escalated. In this deterministic sim misses don't come "
               "from thresholds — they'd come from missing detectors.")
    n = st.slider("Interactions to simulate", 20, 200, 60, 10)
    if st.button("▶ Run audit", type="primary"):
        rng = random.Random(42)
        pool = [(q, True) for q in RISKY] + [(q, False) for q in SAFE]
        rows, caught, fp, abstains, similarity_checks = [], 0, 0, 0, 0
        latencies = []
        n_risky = n_safe = 0
        for q, risky in (rng.choice(pool) for _ in range(n)):
            r = run_pipeline(q, profile_key=profile_key, geo_key=geo_key,
                             threshold=eff_threshold)
            flagged = r.decision.action != DG.ALLOW
            latencies.append(r.total_ms)
            similarity_checks += r.receipt["performance"]["semantic_similarity_checks"]
            if r.decision.action == DG.ABSTAIN:
                abstains += 1
            if risky:
                n_risky += 1
                caught += flagged
            else:
                n_safe += 1
                fp += flagged
            rows.append({"query": q[:44], "risky?": "yes" if risky else "no",
                         "action": r.decision.action, "ms": round(r.total_ms)})
        recall = caught / n_risky * 100 if n_risky else 0
        fp_rate = fp / n_safe * 100 if n_safe else 0
        latency = latency_percentiles(latencies)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Risky caught", f"{recall:.0f}%")
        m2.metric("False alarms on safe", f"{fp_rate:.0f}%")
        m3.metric("Abstained", f"{abstains}/{n}")
        m4.metric("p50 latency", f"{latency['p50_ms']:.0f} ms")
        m5.metric("p95 latency", f"{latency['p95_ms']:.0f} ms")
        st.bar_chart({"caught": caught, "false_alarms": fp, "abstains": abstains})
        st.dataframe(rows[::-1], use_container_width=True, height=280)
        st.caption(f"Local similarity checks: **{similarity_checks}** · external LLM calls: **0** · "
                   "tokens: **0** · estimated LLM cost: **$0.00**")

# ------------------------------------------------------------------ about tab
with tab_about:
    st.markdown("### What this demonstrates")
    st.markdown("""
| Round-2 requirement | Where it lives |
|---|---|
| Different risk/latency per use case | Sidebar profiles → Risk Router + budgets per path |
| No real-time ground truth | Evidence Orchestrator builds Verified Fact State from systems of record |
| Overlapping risks (hallucination+privacy) | PII Guard + typed verifiers on the same request flow |
| Over/under-flagging tradeoff | Strictness slider ↔ Batch audit FP/fatigue metrics |
| Tiered decisions + human rules | Decision Gate: ALLOW/EDIT/BLOCK/ABSTAIN/ESCALATE/HUMAN_CONFIRM |
| Configurable policy layer + audit trail | Jurisdiction overlay + downloadable receipts (`logs/receipts.jsonl`) |
| Feedback loops | Review outcomes persist a separate threshold offset per intent |
| Metrics & monitoring | Batch audit: recall, false-alarm %, p50/p95 latency, similarity checks, zero LLM spend |
""")
    st.markdown("### Run it yourself")
    st.code("./run.sh\n# or\ncd prototype && uv venv .venv && uv pip install -r requirements.txt && .venv/bin/streamlit run app.py")
    st.caption("All data is simulated. The prototype uses deterministic checks and a local similarity scorer. It makes no external LLM calls.")
