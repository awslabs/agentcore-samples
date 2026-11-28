import os
import unittest
from unittest.mock import patch

from dynatrace import export_config


class AgentIsolationTest(unittest.TestCase):
    def test_requests_do_not_reuse_agent_history(self):
        with patch.dict(os.environ, AWS_DEFAULT_REGION="us-east-1", AWS_EC2_METADATA_DISABLED="true"):
            import travel_agent
            from starlette.exceptions import HTTPException

        from types import SimpleNamespace
        from unittest.mock import Mock

        first = Mock(return_value=SimpleNamespace(message={"content": [{"text": "first answer"}]}))
        second = Mock(return_value=SimpleNamespace(message={"content": [{"text": "second answer"}]}))
        with patch.object(travel_agent, "Agent", side_effect=[first, second]) as factory:
            with self.assertRaises(HTTPException) as err:
                travel_agent.strands_agent_bedrock({"prompt": " "})
            self.assertEqual(err.exception.status_code, 400)
            factory.assert_not_called()
            self.assertEqual(travel_agent.strands_agent_bedrock({"prompt": "first"}), "first answer")
            self.assertEqual(travel_agent.strands_agent_bedrock({"prompt": "second"}), "second answer")
            self.assertEqual(factory.call_count, 2)
        first.assert_called_once_with("first")
        second.assert_called_once_with("second")


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
