"""STEP 7 — Final wording guard.

The last gate before send: catches unsafe/biased phrasing even when the
facts themselves were proven. Rewrites neutral, logs every change.
"""
import re

RULES = [
    (re.compile(r"\bobviously careless with repayments\b", re.I),
     "varied repayment behaviour across"),
    (re.compile(r"\bcareless\s+with\s+repayments\b", re.I),
     "varied in repayment behaviour"),
    (re.compile(r"\bcareless\s+with\s+money\b", re.I),
     "varied in handling finances"),
    (re.compile(r"\balways\s+default\b", re.I),
     "sometimes face repayment challenges"),
    (re.compile(r"\bnever\s+repay\b", re.I),
     "show varied repayment behaviour"),
    (re.compile(r"\bare\s+bad\s+with\s+(money|numbers|math)\b", re.I),
     "have varied capability with finances"),
    (re.compile(r"\bwomen\s+are\s+bad\s+with", re.I),
     "financial capability varies across all customer segments; women are"),
    (re.compile(r"\bgirls\s+are\s+bad", re.I),
     "financial capability varies across all customer segments"),
    (re.compile(r"\bstupid\s+question\b|\bdumb\s+question\b", re.I),
     "question"),
    (re.compile(r"\byou wouldn'?t understand\b", re.I),
     "this is commonly misunderstood"),
    (re.compile(r"\bobviously\b", re.I), "typically"),
    (re.compile(r"\bas you people know\b", re.I), "as you may know"),
    (re.compile(r"\b(those people|you people)\b", re.I), "customers"),
]

GROUP_HINT = re.compile(r"\b(rural customers?|villagers|farmers)\b", re.I)


def guard(text: str):
    """Return (clean_text, list_of_patterns_triggered)."""
    flags = []
    for rx, rep in RULES:
        found = rx.search(text)
        if found:
            flags.append(found.group(0))
            text = rx.sub(rep, text)
    return text, flags
