"""SIMULATED SYSTEMS OF RECORD.

Everything here pretends to be the bank's authoritative state:
Loan DB, signed Contract store, Policy repository, CRM notes,
product KB and a customer PII vault. Deterministic so the demo
always tells the same story. Reference 'today' is pinned.
"""
from datetime import date

TODAY = date(2026, 8, 22)

# ------------------------------------------------------------------ Loan DB
LOAN_DB = {
    "LN-2024-00881": {
        "customer_id": "CUST-101",
        "product": "home_loan",
        "principal_inr": 2_000_000,
        "status": "active",
        "disbursed_on": "2025-05-10",   # ~15 months ago
        "region": "IN",
        "remaining_tenure_months": 225,
    },
    "LN-2026-01047": {
        "customer_id": "CUST-102",
        "product": "home_loan",
        "principal_inr": 4_500_000,
        "status": "active",
        "disbursed_on": "2026-06-15",   # ~2 months ago (inside lock-in)
        "region": "IN",
        "remaining_tenure_months": 238,
    },
    "LN-2025-01212": {
        "customer_id": "CUST-103",
        "product": "home_loan",
        "principal_inr": 3_000_000,
        "status": "active",
        "disbursed_on": "2025-12-22",   # 8 months ago
        "region": "IN",
        "remaining_tenure_months": 172,
    },
}

# --------------------------------------------------------- Contract store
CONTRACTS = {
    "LN-2024-00881": [{
        "doc_id": "CTR-881",
        "version": "v2 (2025-05)",
        "age_days": 3,
        "text": ("Foreclosure permitted after a 6-month lock-in. A fee of 1.5% of "
                 "sanctioned principal applies if closed within 24 months of "
                 "disbursement; thereafter nil."),
        "terms": {
            "lockin_months": 6,
            "fee_pct_within_24m": 1.5,
            "fee_pct_after_24m": 0.0,
        },
    }],
    "LN-2026-01047": [{
        "doc_id": "CTR-1047",
        "version": "v1 (2026-06)",
        "age_days": 5,
        "text": ("A 12-month lock-in applies. Foreclosure during lock-in is not "
                 "permitted except on death of the borrower."),
        "terms": {"lockin_months": 12},
    }],
    "LN-2025-01212": [{
        "doc_id": "CTR-1212",
        "version": "v1 (2025-12)",
        "age_days": 4,
        "text": ("A 12-month lock-in applies. Foreclosure during lock-in is not "
                 "permitted. A fee of 1.5% of sanctioned principal applies if "
                 "closed within 24 months; thereafter nil."),
        "terms": {
            "lockin_months": 12,
            "fee_pct_within_24m": 1.5,
            "fee_pct_after_24m": 0.0,
        },
    }],
}

# ------------------------------------------------------------ Policy repo
FEE_POLICY = {
    "policy_id": "POL-FEE-2026Q3",
    "version": "2026-Q3",
    "effective": "2026-07-01",
    "age_days": 2,
    "rules": {
        "lockin_months": 6,
        "fee_pct_within_24m": 1.5,
        "fee_pct_after_24m": 0.0,
    },
}

WAIVER_POLICY = {
    "policy_id": "POL-WAV-001",
    "version": "v5",
    "age_days": 40,
    "rule": ("Foreclosure-fee waivers require documented Branch Manager approval. "
             "Digital assistants may NOT commit a waiver."),
}

WEALTH_DOCS = [{
    "fact_key": "wealth.apr_schedule",
    "doc_id": "DOC-APR-Q1",
    "version": "2026-Q1",
    "age_days": 120,          # stale on arrival
    "value": "Managed portfolio APR schedule (2026-Q1 snapshot)",
}]

# -------------------------------------------------------------- CRM notes
CRM_NOTES = {
    "CUST-101": [{
        "note_id": "N-55",
        "age_days": 1,
        "text": "Previous chat agent told the customer the foreclosure fee is "
                "'automatically waived' for salary accounts.",
    }]
}

# ------------------------------------------------------------- Product KB
PRODUCT_KB = {
    "branch_hours": {"value": "Mon–Fri 09:00–17:00 IST · Sat 10:00–14:00",
                     "ref": "KB-100", "age_days": 0},
    "home_loan_rates": {"value": "8.35% p.a. floating (rate card 2026-07)",
                        "ref": "KB-210", "age_days": 2},
}

# -------------------------------------------------------------- PII vault
CUSTOMER_PII = {
    "CUST-101": {"name": "Rahul Sharma", "phone": "+91-9876504421",
                 "email": "rahul.s@example.com"},
    "CUST-102": {"name": "Meera Iyer", "phone": "+91-9811123345",
                 "email": "meera.i@example.com"},
}

# --------------------------------------------------------- Offers engine
# Used for the fuzzy balance-transfer eligibility check (local similarity scorer).
# The offer terms are unstructured at the source boundary. The local similarity
# scorer matches them to authoritative borrower fields, including tenure.
OFFERS_ENGINE = {
    "BT-07": {
        "offer_id": "OFFERS:BT-07",
        "version": "2026-08",
        "age_days": 1,
        "terms": {
            "eligible_products": ["home_loan"],
            "eligible_regions": ["IN"],
            "min_outstanding_inr": 100_000,
            "min_remaining_tenure_months": 12,
            "rate_pct": 8.25,
            "rate_type": "fixed 3y",
        },
        "raw_text": "Balance transfer BT-07: home loans in IN, min outstanding 1L, remaining tenure 12m+, 8.25% fixed 3y",
    }
}
