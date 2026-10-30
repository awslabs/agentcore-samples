from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent" / "container"
sys.path.insert(0, str(AGENT_ROOT))

import main
from fastapi.testclient import TestClient


class FakeResult:
    message = "ok"
    metrics = None


class MainTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_ping(self):
        response = self.client.get("/ping")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "healthy"})

    def test_invocations_accepts_input_prompt(self):
        with patch("main.create_agent", return_value=lambda prompt: FakeResult()):
            response = self.client.post(
                "/invocations",
                json={
                    "input": {
                        "prompt": "hello",
                        "payment_context": {
                            "user_id": "user",
                            "payment_session_id": "session",
                            "payment_instrument_id": "instrument",
                        },
                    }
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output"]["message"], "ok")

    def test_missing_payment_context_returns_400(self):
        response = self.client.post("/invocations", json={"input": {"prompt": "hello"}})

        self.assertEqual(response.status_code, 400)
        self.assertIn("payment_context", response.text)

    def test_malformed_payment_context_returns_400(self):
        response = self.client.post(
            "/invocations",
            json={"input": {"prompt": "hello", "payment_context": "not-a-mapping"}},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("payment_context must be an object", response.text)

    def test_incomplete_payment_context_returns_400(self):
        response = self.client.post(
            "/invocations",
            json={
                "input": {
                    "prompt": "hello",
                    "payment_context": {"user_id": "user"},
                }
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("payment_session_id", response.text)


if __name__ == "__main__":
    unittest.main()
