"""Use-case profiles + geography policy overlays.

Different AI use cases carry different risk tolerance and latency budgets —
the same gate must serve all of them.
"""

PROFILES = {
    "customer_chatbot": {
        "icon": "⚡", "budget_ms": 800, "strictness": 0.35,
        "blurb": "Public-facing assistant. Speed matters; only high-stakes intents hit the full gate.",
    },
    "internal_assistant": {
        "icon": "🧑‍💼", "budget_ms": 2000, "strictness": 0.60,
        "blurb": "Employee copilot. Balanced checks; evidence receipt kept for audit.",
    },
    "decision_desk": {
        "icon": "🧐", "budget_ms": 5000, "strictness": 0.85,
        "blurb": "Regulated decision support. Every claim verified; bias toward escalation.",
    },
}

GEOS = {
    "India (DPDP Act 2023 + RBI Fair Practices)": {
        "code": "IN", "pii_law": "DPDP Act 2023",
        "credit_rule": "RBI Master Direction – Fair Practices Code",
        "retention_days": 365, "receipt_note": "Receipt retained 365 days per bank policy.",
    },
    "EU (GDPR + EU AI Act)": {
        "code": "EU", "pii_law": "GDPR Art. 6 lawful basis required",
        "credit_rule": "EU AI Act — credit scoring = high-risk class",
        "retention_days": 90, "receipt_note": "Receipt minimised; 90-day retention.",
    },
    "US (GLBA + SR 11-7)": {
        "code": "US", "pii_law": "GLBA Safeguards Rule",
        "credit_rule": "SR 11-7 model risk management",
        "retention_days": 2555, "receipt_note": "Receipt archived 7 years per GLBA.",
    },
}
