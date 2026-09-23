"""Unit tests for the Trigger Lambda's pure email-parsing helpers.

Covers `parse_email` and `is_email_format` in lambdas/trigger/handler.py — the
stdlib-only functions that turn an S3 object into a structured prompt. The
handler imports boto3 (lazy clients, no network at import), so importing it is
safe without AWS credentials.

Run:
    python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "trigger"))

from handler import is_email_format, parse_email  # noqa: E402


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
        headers, body = parse_email("just a body with no headers")
        self.assertEqual(headers, {})

    def test_ignores_unrecognized_headers(self):
        content = "From: a@b.com\nX-Custom: ignore-me\n\nbody"
        headers, _ = parse_email(content)
        self.assertIn("from", headers)
        self.assertNotIn("x-custom", headers)


if __name__ == "__main__":
    unittest.main()
