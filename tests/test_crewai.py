"""Tests for pushary_crewai. Framework-free: CrewAI is never imported here (the tool
factory imports it lazily), so the core helpers are exercised without it installed.
"""

import hashlib
import hmac
import json
import unittest

import pushary_crewai as pc


class FakeDecisions:
    def __init__(self, ask_result=None):
        self.ask_calls = []
        self._ask_result = ask_result or {}

    def ask(self, question, **kwargs):
        self.ask_calls.append({"question": question, **kwargs})
        return self._ask_result


class FakeClient:
    def __init__(self, decisions=None, enroll_result=None):
        self.decisions = decisions or FakeDecisions()
        self._enroll_result = enroll_result or {}
        self.enroll_calls = []

    def enroll(self, external_id):
        self.enroll_calls.append(external_id)
        return self._enroll_result


class WithFakeClient:
    def __init__(self, client):
        self.client = client
        self._orig = None

    def __enter__(self):
        self._orig = pc._client
        pc._client = lambda *a, **k: self.client
        return self.client

    def __exit__(self, *exc):
        pc._client = self._orig


SECRET = "whsec_test"


def sign(body: str) -> str:
    return hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


class ConnectTests(unittest.TestCase):
    def test_connect_returns_universal_link(self):
        client = FakeClient(enroll_result={"universalLink": "https://pushary.com/e/tok"})
        with WithFakeClient(client):
            self.assertEqual(pc.connect("user_1"), "https://pushary.com/e/tok")


class AskHumanTests(unittest.TestCase):
    def test_ask_human_forwards_deterministic_key_and_returns_dict(self):
        decisions = FakeDecisions(ask_result={"answered": True, "value": "yes", "approved": True})
        with WithFakeClient(FakeClient(decisions=decisions)):
            out = pc.ask_human("Approve?", external_id="user_1", node="gate")
        self.assertTrue(out["approved"])
        self.assertEqual(
            decisions.ask_calls[0]["idempotency_key"],
            pc.deterministic_key(["user_1", "gate", "Approve?"]),
        )


class MakeToolTests(unittest.TestCase):
    def test_factory_lazily_imports_crewai(self):
        # CrewAI is not installed in this env, so building the tool raises ImportError
        # from the lazy import (proving the module itself loads without CrewAI).
        with self.assertRaises(ImportError):
            pc.make_ask_human_tool("user_1")


class DescribeAnswerTests(unittest.TestCase):
    def test_formats_every_outcome(self):
        self.assertIn("approved", pc.describe_answer("confirm", {"answered": True, "approved": True}))
        self.assertIn("declined", pc.describe_answer("confirm", {"answered": True, "approved": False}))
        self.assertIn("NOT approved", pc.describe_answer("confirm", {"answered": False, "status": "expired"}))
        self.assertIn("B", pc.describe_answer("select", {"answered": True, "value": "B"}))


class ResolveCallbackTests(unittest.TestCase):
    def test_verifies_and_folds_approved(self):
        body = json.dumps({"correlationId": "d1", "answer": "yes", "answeredAt": ""})
        self.assertTrue(pc.resolve_pushary_callback(body, sign(body), SECRET)["approved"])

    def test_rejects_bad_signature(self):
        body = json.dumps({"correlationId": "d1", "answer": "yes", "answeredAt": ""})
        self.assertIsNone(pc.resolve_pushary_callback(body, "nope", SECRET))


class IsAffirmativeTests(unittest.TestCase):
    def test_fail_closed(self):
        self.assertTrue(pc.is_affirmative("yes"))
        self.assertFalse(pc.is_affirmative("no"))
        self.assertFalse(pc.is_affirmative(None))


if __name__ == "__main__":
    unittest.main()
