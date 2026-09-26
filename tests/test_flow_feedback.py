"""Exercise the phone review example with CrewAI's real pause/resume runtime."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

CREWAI_INSTALLED = importlib.util.find_spec("crewai") is not None


class FakeDecisions:
    def __init__(self) -> None:
        self.created: dict[str, object] = {}
        self.status = "pending"
        self.value: str | None = None

    def create(self, question: str, **kwargs: object) -> dict[str, str]:
        self.created = {"question": question, **kwargs}
        return {"decisionId": "decision-1", "status": "pending"}

    def get(self, decision_id: str) -> dict[str, object]:
        return {
            "decisionId": decision_id,
            "context": self.created["context"],
            "question": self.created["question"],
            "externalId": self.created["external_id"],
            "type": "confirm",
            "status": self.status,
            "answered": self.status == "answered",
            "value": self.value,
        }


class FakeClient:
    def __init__(self, decisions: FakeDecisions) -> None:
        self.decisions = decisions


@unittest.skipUnless(CREWAI_INSTALLED, "needs crewai: pip install crewai")
class FlowFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.import_storage = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"CREWAI_STORAGE_DIR": cls.import_storage.name}):
            from examples import flow_feedback

        cls.example = flow_feedback

    @classmethod
    def tearDownClass(cls) -> None:
        cls.import_storage.cleanup()

    def setUp(self) -> None:
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.environment = patch.dict(
            os.environ,
            {
                "CREWAI_STORAGE_DIR": self.storage.name,
                "PUSHARY_EXTERNAL_ID": "customer-1",
                "PUSHARY_API_KEY": "pk_test.sk_test",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.decisions = FakeDecisions()
        client_patch = patch.object(self.example, "pushary_client", return_value=FakeClient(self.decisions))
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def start_review(self) -> tuple[str, str]:
        started = self.example.start_review("Send the release note")
        self.assertEqual(started["decision_id"], "decision-1")
        self.assertEqual(self.decisions.created["external_id"], "customer-1")
        self.assertIs(self.decisions.created["require_reachable"], True)
        return started["flow_id"], started["decision_id"]

    def test_approval_resumes_saved_flow_once(self) -> None:
        flow_id, decision_id = self.start_review()
        database = Path(self.storage.name) / "claims.sqlite"
        self.assertEqual(
            self.example.resume_review(flow_id, decision_id, database), {"status": "pending"}
        )
        self.decisions.status = "answered"
        self.decisions.value = "yes"
        resumed = self.example.resume_review(flow_id, decision_id, database)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(
            resumed["result"], {"approved": True, "proposal": "Send the release note"}
        )
        self.assertEqual(self.example.resume_review(flow_id, decision_id, database), resumed)
        with self.assertRaisesRegex(ValueError, "different decision"):
            self.example.resume_review(flow_id, "different-decision", database)

    def test_decline_and_expiry_fail_closed(self) -> None:
        for status, value in (("answered", "no"), ("expired", None)):
            with self.subTest(status=status):
                flow_id, decision_id = self.start_review()
                self.decisions.status = status
                self.decisions.value = value
                result = self.example.resume_review(
                    flow_id, decision_id, Path(self.storage.name) / "claims.sqlite"
                )
                self.assertEqual(result["result"]["approved"], False)

    def test_mismatched_decision_cannot_resume(self) -> None:
        flow_id, decision_id = self.start_review()
        self.decisions.status = "answered"
        self.decisions.value = "yes"
        self.decisions.created["context"] = "crewai:some-other-flow:propose"
        database = Path(self.storage.name) / "claims.sqlite"
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.example.resume_review(flow_id, decision_id, database)
        context = self.example.ReviewFlow.from_pending(flow_id).pending_feedback
        self.decisions.created["context"] = self.example.review_context(context, "customer-1")
        self.assertTrue(self.example.resume_review(flow_id, decision_id, database)["result"]["approved"])

    def test_changed_recipient_cannot_resume_when_api_omits_external_id(self) -> None:
        flow_id, decision_id = self.start_review()
        self.decisions.status = "answered"
        self.decisions.value = "yes"
        self.decisions.created["external_id"] = None
        os.environ["PUSHARY_EXTERNAL_ID"] = "customer-2"
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.example.resume_review(
                flow_id, decision_id, Path(self.storage.name) / "claims.sqlite"
            )


if __name__ == "__main__":
    unittest.main()
