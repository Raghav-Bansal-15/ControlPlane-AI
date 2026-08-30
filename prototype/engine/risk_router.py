"""STEP 1 — Risk Router.

Classifies every request BEFORE generation: intent, risk level,
score and human-readable reasons. Strictness is not one-size-fits-all:
a branch-hours question and a foreclosure question must travel
different paths with different latency budgets.
"""
import re
from dataclasses import dataclass, field

LOAN_ID_RE = re.compile(r"LN-\d{4}-\d{4,5}", re.I)

# ---- PII
PII_WORD = re.compile(r"\b(phone|mobile|contact\s*number|email|address|aadhaar|aadhar|pan|otp)\b", re.I)
BROAD_PII_REQ = re.compile(
    r"\b(share|give|tell|send|read\s*out|show|provide|export|exporting|list|disclose|on\s*file"
    r"|have\s*on\s*file|what|do\s*you\s*have|registered|linked\s*to|associated|every|all\s*customers?|all\b.*borrowers?)\b",
    re.I,
)
BULK_PII = re.compile(r"\b(export|every|all|bulk)\b.*\b(phone|mobile|email)\b|\b(phone|mobile|email)\b.*\b(export|every|all|bulk)\b", re.I)

# ---- Balance transfer (before account_action to avoid digit clash)
BALANCE_XFER = re.compile(r"\bbalance[-\s]?transfer\b|\btransfer\s*my\s*loan\b|\bbt[-\s]*eligible\b|\bbt[-\s]*0?7\b", re.I)

# ---- Irreversible account actions
DEBIT_ACCOUNT = re.compile(r"\bdebit\b.*\b(account|savings|linked\s*account)\b|\b(account|savings)\b.*\bdebit\b", re.I)
TRANSFER_MONEY = re.compile(r"\b(transfer|pay\s*now|initiate|execute|approve)\b.*(?:₹|\bfunds\b|\bamount\b|\brs\.?\b|\blakh\b|\bcrore\b)", re.I)

AMT = re.compile(r"₹|\brs\.?\b|\blakh\b|\bcr(ore)?\b|\d{5,}", re.I)
WAIVE = re.compile(r"\b(waive\w*|waiver)\b", re.I)

# ---- Foreclosure / fee verbs + product signals
VERB_FIN = re.compile(
    r"\b(close|closure|foreclos\w*|forclose\w*|prepay\w*|penalt\w*|waiv\w*|waiver|charges?|settl\w*|redeem\w*|pay\s*off|payoff|shut\s*down|exit\s*charge|exit\s*fee|\bclear\w*\b)\b",
    re.I,
)
LOAN_NOUN = re.compile(r"\b(loans?|accounts?|fds?|deposits?|emi|principal|tenure|outstanding|balances?)\b", re.I)
FINANCE_TERM = re.compile(r"\b(home\s*loans?|housing\s*loans?|housing\s*finance|home\s*lone)\b", re.I)

# Wealth: any mention of wealth/portfolio is enough (narrow enough not to catch home-loan rates)
WEALTH_SPECIFIC = re.compile(r"\b(wealth|portfolio)\b", re.I)
RECOMMEND = re.compile(r"\b(should i|which is better|recommend|suggest|compare|switch to|invest in)\b", re.I)
# Branch hours: needs branch/office or hours/timings — loan "close" alone no longer triggers it
BRANCH_HOURS = re.compile(r"\b(branch|office)\b.*\b(hours?|timings?|open|opening|close|closing|location)\b|\b(hours?|timings?)\b", re.I)


@dataclass
class RiskVerdict:
    level: str            # LOW | MEDIUM | HIGH
    score: float          # 0..1
    intent: str
    reasons: list = field(default_factory=list)
    confidence: float = 0.9


def actionable_waiver(q: str) -> bool:
    ql = q.lower()
    # waive verb + explicit apply/process demand
    if re.search(r"\bwaiv", ql):
        if re.search(r"\b(apply|process|commit|just\s+waive|waive\s+for\s+me|waive.*for\s+me|do\s+it|execute|approve|signed\s+off|process\s+that\s+waiver|just\s+waive)\b", ql):
            return True
        # also "waive my rs 30,000 fee" is actionable even without apply/process
        if re.search(r"\bwaive\s+my\b|\bwaive\s+the\s+foreclosure\s+fee\b", ql):
            return True
    # imperative close with zero/without-fee/salary claim — demand to close at zero
    if re.search(r"\bclose\b", ql) and re.search(r"\b(zero\s*charges?|without\s*fee|without\s*penalty|at\s*zero\s*cost|salary\s*account)\b", ql):
        if re.search(r"close.*with\s*zero|close.*without|at\s*zero\s*cost|with\s*zero\s*charges", ql):
            # distinguish from "can I close ... without penalty?" which is a fee question,
            # not a waiver demand: that phrasing lacks salary/zero-charges demand language?
            # For "without penalty?" we want loan_foreclosure path, not fee_waiver.
            # So only treat as fee_waiver when salary or zero-charges demanded, not plain "without penalty"
            if re.search(r"salary|zero\s*charges|at\s*zero|with\s*zero", ql):
                return True
    return False


def route(query: str) -> RiskVerdict:
    q = query.strip()

    # 1) PII — most sensitive, check first
    if PII_WORD.search(q) and (BROAD_PII_REQ.search(q) or BULK_PII.search(q)):
        return RiskVerdict("HIGH", 0.95, "privacy_disclosure",
                           ["Request targets personal data fields",
                            "Outbound disclosure to a possibly unverified channel"], 0.93)
    if BULK_PII.search(q):
        return RiskVerdict("HIGH", 0.96, "privacy_disclosure",
                           ["Bulk personal-data export request detected"], 0.94)

    # (Home-loan rates KB handled by pipeline fast path via _kb_key_for_query;
#  router foreclosure/fee block handles high-risk variants correctly)

    # 2) Balance transfer before account_action to avoid digit clash
    if BALANCE_XFER.search(q):
        return RiskVerdict("MEDIUM", 0.6, "balance_transfer",
                           ["Product-switch request — eligibility + cost checks",
                            "one claim needs fuzzy matching → local similarity scorer"], 0.86)

    # 3) Irreversible account actions (human confirm)
    if DEBIT_ACCOUNT.search(q) or TRANSFER_MONEY.search(q):
        return RiskVerdict("HIGH", 0.92, "account_action",
                           ["Irreversible money-movement instruction detected",
                            "Multi-turn agent risk: one bad action compounds downstream"], 0.9)

    # 4) Foreclosure / fee / settlement intents
    has_verb = bool(VERB_FIN.search(q))
    has_product = bool(LOAN_NOUN.search(q) or FINANCE_TERM.search(q) or LOAN_ID_RE.search(q))
    if has_verb and has_product:
        if actionable_waiver(q):
            return RiskVerdict("HIGH", 0.90 + (0.05 if AMT.search(q) else 0), "fee_waiver",
                               ["Financial-impact query (fees/waivers)",
                                "High-impact vs low-impact asymmetry applies"], min(0.97, 0.90 + (0.05 if AMT.search(q) else 0)))
        # waive-like but not clearly actionable already handled via actionable_waiver
        # keep explicit actionable waive patterns with adjacency
        if WAIVE.search(q) and has_product and re.search(r"\b(waive|waiver)\b.*\b(fee|loan)\b|\b(fee|loan)\b.*\b(waive|waiver)\b", q, re.I):
            if re.search(r"\b(just\s+waive|waive\s+the\s+foreclosure\s+fee|apply.*waiver|process.*waiver|waive\s+my\s+rs)\b", q, re.I):
                return RiskVerdict("HIGH", 0.90, "fee_waiver",
                                   ["Financial-impact query (fees/waivers)",
                                    "High-impact vs low-impact asymmetry applies"], 0.92)
        return RiskVerdict("HIGH", 0.88 + (0.05 if AMT.search(q) else 0), "loan_foreclosure",
                           ["Contract-altering financial query",
                            "Wrong answer = financial loss, not inconvenience"], min(0.96, 0.88 + (0.05 if AMT.search(q) else 0)))

    # 5) Wealth portfolio APR — wealth/portfolio is enough
    if WEALTH_SPECIFIC.search(q):
        return RiskVerdict("MEDIUM", 0.55, "wealth_rate_query",
                           ["Rate/quote request — depends on freshness-sensitive docs"], 0.85)

    if RECOMMEND.search(q):
        return RiskVerdict("MEDIUM", 0.5, "recommendation",
                           ["Advice-shaped query — suitability rules may apply"], 0.82)

    # 5b) Simple home-loan rate queries — LOW fast path
    # Match anywhere in query when it's clearly about home loan rates
    if re.search(r"\bwhat\s+(is|are)\s+(the\s+)?(current\s+)?(home\s*loan|housing\s*loan)\s+(rate|rates|interest)\b", q, re.I) \
       or re.search(r"\b(home\s*loan|housing\s*loan)\s+(rate|rates|interest)\b", q, re.I) \
       or re.search(r"\bcurrent\s+(home\s*loan|housing\s*loan)\s+(rate|rates|interest)\b", q, re.I) \
       or re.search(r"\brate\s+(of|for)\s+(home\s*loan|housing\s*loan)\b", q, re.I):
        return RiskVerdict("LOW", 0.2, "branch_info",
                           ["Generic public information"], 0.95)

    # Branch hours: needs branch/office keyword + hours/timings — loan "close" alone no longer triggers it
    # Also: if query has any financial risk signals, don't treat as branch_info
    if BRANCH_HOURS.search(q):
        # Check for financial risk words — if present, this is NOT a simple branch hours query
        if re.search(r"\b(close|closure|foreclos|forclose|prepay|penalt|waiv|charges?|fee|settl|redeem|pay\s*off|payoff|debit|transfer|balance)\b", q, re.I):
            pass  # fall through to financial handling
        else:
            return RiskVerdict("LOW", 0.2, "branch_info",
                               ["Generic public information"], 0.95)

    # 6) Waiver-specific queries without explicit product nouns — route to fee_waiver for safety
    # Catches "apply the waiver", "waive my fee", "zero cost closure" etc. even when no "loan" noun present
    if re.search(r"\b(apply|process)\s+(the\s+)?(waiver|fee\s+waiver)\b", q, re.I) \
       or re.search(r"\bwaive\s+(my|the)\s+(foreclosure\s+)?fee\b", q, re.I) \
       or re.search(r"\bclose.*with\s*zero|close.*without\s*fee|at\s*zero\s*cost\b", q, re.I):
        return RiskVerdict("HIGH", 0.85, "fee_waiver",
                           ["Waiver-related demand without explicit product reference",
                            "High-impact vs low-impact asymmetry applies"], 0.88)

    # 7) Partial financial signal — not fully matched but contains finance keywords → treat as unknown financial (fail-safe)
    if LOAN_ID_RE.search(q) or FINANCE_TERM.search(q) or VERB_FIN.search(q) or LOAN_NOUN.search(q):
        if re.search(r"\b(loan|deposit|fd|account|charges?|penalt|fee|waiv|close|foreclos|gold\s*loan|car\s*loan|home\s*loan)\b", q, re.I):
            # but home-loan rates already returned above, so remaining finance signals are genuinely unclassified
            return RiskVerdict("MEDIUM", 0.45, "unknown_financial",
                               ["Partial financial signal — unclassified intent, fail-safe to abstain"], 0.62)

    return RiskVerdict("LOW", 0.25, "generic_info",
                       ["No high-risk signals matched"], 0.7)
