import os
import unittest
from unittest.mock import patch

from dynatrace import export_config


class ExportConfigTest(unittest.TestCase):
    def test_reject_endpoint_before_reading_token(self):
        for url in (
            "",
            "http://collector",
            "https://",
            "https://user@collector",
            "https://collector?token=value",
            "https://collector/#fragment",
            "https://collector:bad",
            "https://collector\\@other",
        ):
            with (
                self.subTest(url=url),
                patch.dict(os.environ, OTEL_ENDPOINT=url),
                patch("dynatrace.Path.read_text") as read,
            ):
                with self.assertRaisesRegex(ValueError, "OTEL_ENDPOINT"):
                    export_config()
                read.assert_not_called()

    @patch.dict(os.environ, OTEL_ENDPOINT="https://collector.example/api/v2/otlp/", DT_TOKEN="env-fixture")
    def test_token_source(self):
        with patch("dynatrace.Path.read_text", return_value="file-fixture\n"):
            self.assertEqual(
                export_config(), ("https://collector.example/api/v2/otlp", {"Authorization": "Api-Token file-fixture"})
            )
        with patch("dynatrace.Path.read_text", side_effect=FileNotFoundError):
            self.assertEqual(export_config()[1], {"Authorization": "Api-Token env-fixture"})
        with patch("dynatrace.Path.read_text", side_effect=PermissionError), self.assertRaises(PermissionError):
            export_config()

    @patch.dict(os.environ, OTEL_ENDPOINT="https://collector.example")
    @patch("dynatrace.Path.read_text", side_effect=FileNotFoundError)
    def test_missing_or_invalid_token(self, _):
        for token in ("", "line\nbreak", "contains space"):
            with (
                self.subTest(token=token),
                patch.dict(os.environ, DT_TOKEN=token),
                self.assertRaisesRegex(ValueError, "ingest token"),
            ):
                export_config()
