"""Unit tests for the Trigger Lambda's pure email-parsing helpers.

Covers `parse_email` and `is_email_format` in lambdas/trigger/handler.py — the
stdlib-only functions that turn an S3 object into a structured prompt. The
handler creates an S3 client at import time; stub it before importing.
Also checks the actual SigV4 signing path with fake credentials and mocked HTTP.

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from botocore.credentials import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "trigger"))

with patch("boto3.client", return_value=MagicMock()):
    import handler as trigger

is_email_format = trigger.is_email_format
parse_email = trigger.parse_email


class IsEmailFormatTests(unittest.TestCase):
    def test_detects_from_header(self):
        self.assertTrue(is_email_format("From: a@b.com\nSubject: hi\n\nbody"))

    def test_detects_subject_header(self):
        self.assertTrue(is_email_format("Subject: Claim\n\nbody"))

    def test_case_insensitive(self):
        self.assertTrue(is_email_format("from: a@b.com\n\nbody"))

    def test_plain_json_is_not_email(self):
        self.assertFalse(is_email_format('{"policy_number": "POL-1"}'))

    def test_plain_text_is_not_email(self):
        self.assertFalse(is_email_format("Just some text about a claim."))

    def test_empty_is_not_email(self):
        self.assertFalse(is_email_format(""))


class ParseEmailTests(unittest.TestCase):
    def test_extracts_headers_and_body(self):
        content = (
            "From: jane@example.com\n"
            "Subject: Water damage claim\n"
            "Date: Mon, 1 Jun 2026 12:00:00 +0000\n"
            "\n"
            "My basement flooded. Policy POL-67890. Approx $8000."
        )
        headers, body = parse_email(content)
        self.assertEqual(headers["from"], "jane@example.com")
        self.assertEqual(headers["subject"], "Water damage claim")
        self.assertIn("POL-67890", body)
        self.assertNotIn("From:", body)

    def test_body_only_after_blank_line(self):
        content = "From: a@b.com\nSubject: s\n\nline1\nline2"
        _, body = parse_email(content)
        self.assertEqual(body, "line1\nline2")

    def test_headers_lowercased_keys(self):
        content = "FROM: a@b.com\nSUBJECT: Hi\n\nbody"
        headers, _ = parse_email(content)
        self.assertIn("from", headers)
        self.assertIn("subject", headers)

    def test_no_headers_yields_empty_dict(self):
        # No blank line and no recognized headers → body_start stays 0.
        headers, _body = parse_email("just a body with no headers")
        self.assertEqual(headers, {})

    def test_ignores_unrecognized_headers(self):
        content = "From: a@b.com\nX-Custom: ignore-me\n\nbody"
        headers, _ = parse_email(content)
        self.assertIn("from", headers)
        self.assertNotIn("x-custom", headers)


class RuntimeAuthTests(unittest.TestCase):
    def test_trigger_signs_with_iam_not_cognito(self):
        credentials = Credentials("offline-key", "offline-secret", "offline-token")
        session = MagicMock()
        session.get_credentials.return_value = credentials
        response = MagicMock()
        response.status = 200
        response.__iter__.return_value = iter([b"accepted\n"])
        arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/claimsagent-test"
        with (
            patch.object(trigger, "BotocoreSession", return_value=session),
            patch.object(trigger, "RUNTIME_ARN", arn),
            patch.object(trigger.urllib.request, "urlopen") as urlopen,
        ):
            urlopen.return_value.__enter__.return_value = response
            status, preview = trigger.invoke_runtime_async({"prompt": "offline"})
        self.assertEqual((status, preview), (200, ["accepted"]))
        request = urlopen.call_args.args[0]
        self.assertIn("runtime%2Fclaimsagent-test/invocations", request.full_url)
        self.assertTrue(request.get_header("Authorization").startswith("AWS4-HMAC-SHA256 "))
        self.assertIn("/us-west-2/bedrock-agentcore/aws4_request", request.get_header("Authorization"))
        self.assertEqual(request.get_header("X-amz-security-token"), "offline-token")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 65)


if __name__ == "__main__":
    unittest.main()
