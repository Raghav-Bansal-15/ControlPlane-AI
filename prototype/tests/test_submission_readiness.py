"""Submission-readiness regression tests through public application seams."""

import sys
import tempfile
import unittest
import json
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.pipeline import run_pipeline


class ContractAuthorityTests(unittest.TestCase):
    def test_signed_twelve_month_lock_in_blocks_at_month_eight(self):
        result = run_pipeline(
            "Foreclose my home loan LN-2025-01212 today.",
            profile_key="customer_chatbot",
        )

        self.assertEqual("BLOCK", result.decision.action)
        self.assertIn("12-month", result.decision.answer)
        self.assertTrue(
            any("CTR-1212" in item for item in result.decision.rationale),
            result.decision.rationale,
        )


class ConversationContextTests(unittest.TestCase):
    def test_live_query_carries_prior_risk_and_loan_context(self):
        from engine.ui_adapter import run_user_query

        history = [{
            "query": "Process the waiver for LN-2024-00881.",
            "action": "ESCALATE",
            "intent": "fee_waiver",
        }]

        result, entry = run_user_query(
            "When does the branch open?",
            profile_key="customer_chatbot",
            geo_key="India (DPDP Act 2023 + RBI Fair Practices)",
            threshold=0.35,
            history=history,
        )

        self.assertTrue(
            any("compounding caution" in reason for reason in result.receipt["routing"]["reasons"]),
            result.receipt["routing"]["reasons"],
        )
        self.assertEqual("LN-2024-00881", entry["loan_id"])


class FeedbackCalibrationTests(unittest.TestCase):
    def test_reviewer_feedback_persists_per_intent(self):
        from engine.feedback_store import apply_review_feedback, get_delta

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "feedback_state.json"

            approved = apply_review_feedback("fee_waiver", "approved", store_path=store)
            upheld = apply_review_feedback("loan_foreclosure", "upheld", store_path=store)

            self.assertEqual(-0.02, approved)
            self.assertEqual(0.02, upheld)
            self.assertEqual(-0.02, get_delta("fee_waiver", store_path=store))
            self.assertEqual(0.02, get_delta("loan_foreclosure", store_path=store))


class AuditReceiptTests(unittest.TestCase):
    def test_eu_audit_log_stores_full_receipt_without_plaintext_query(self):
        from engine.audit_log import append_receipt, read_receipts, verify_receipt_integrity

        result = run_pipeline(
            "What are the current home loan rates?",
            geo_key="EU (GDPR + EU AI Act)",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.jsonl"
            append_receipt(result.receipt, path=path)
            stored = read_receipts(path=path)

        self.assertEqual(1, len(stored))
        self.assertEqual(result.receipt, stored[0])
        self.assertNotIn("query", stored[0]["request"])
        self.assertNotIn("What are the current home loan rates?", json.dumps(stored[0]))
        self.assertEqual(64, len(stored[0]["integrity_sha256"]))
        self.assertTrue(verify_receipt_integrity(stored[0]))


class TelemetryTruthfulnessTests(unittest.TestCase):
    def test_similarity_check_is_not_reported_as_an_llm_call(self):
        result = run_pipeline("Am I eligible for a balance transfer of LN-2024-00881?")
        performance = result.receipt["performance"]

        self.assertEqual(1, performance["semantic_similarity_checks"])
        self.assertEqual(0, performance["llm_judge_calls"])
        self.assertEqual(0, performance["llm_tokens"])
        self.assertEqual(0.0, performance["estimated_llm_cost_usd"])
        self.assertNotIn("semantic_llm_judge_calls", performance)

    def test_batch_latency_reports_p50_and_p95(self):
        from engine.metrics import latency_percentiles

        self.assertEqual({"p50_ms": 20.0, "p95_ms": 40.0}, latency_percentiles([10, 20, 30, 40]))


class CustomerAnswerTests(unittest.TestCase):
    def test_balance_transfer_allow_returns_the_verified_eligibility_result(self):
        result = run_pipeline("Am I eligible for a balance transfer of LN-2024-00881?")

        self.assertEqual("ALLOW", result.decision.action)
        self.assertIn("eligible", result.decision.answer.lower())
        self.assertIn("8.25%", result.decision.answer)
        self.assertIn("remaining tenure", result.decision.answer.lower())
        self.assertNotEqual("Verified answer ready.", result.decision.answer)


if __name__ == "__main__":
    unittest.main()
