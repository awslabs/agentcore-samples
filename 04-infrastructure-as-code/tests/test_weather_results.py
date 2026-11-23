"""Offline contracts for the CDK and Terraform weather runtimes (no service calls)."""

import asyncio
import importlib.util
import os
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    "AWS_REGION": "us-west-2",
    "BROWSER_ID": "example-browser",
    "CODE_INTERPRETER_ID": "example-code",
    "MEMORY_ID": "example-memory",
    "RESULTS_BUCKET": "example-results",
    "AWS_EC2_METADATA_DISABLED": "true",
    "ANONYMIZED_TELEMETRY": "false",
    "BROWSER_USE_CLOUD_SYNC": "false",
}


def load_apps():
    for directory in ("cdk/python", "terraform"):
        path = ROOT / directory / "end-to-end-weather-agent/agent-code/weather_agent.py"
        spec = importlib.util.spec_from_file_location(f"weather_{path.parts[-4]}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


class WeatherToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, CONFIG)
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.apps = list(load_apps())

    def test_only_scenario_tools_registered(self):
        for app in self.apps:
            self.assertEqual(
                set(app.create_weather_agent().tool_names),
                {
                    "get_weather_data",
                    "generate_analysis_code",
                    "execute_code",
                    "get_activity_preferences",
                    "save_activity_plan",
                },
            )

    def test_save_uses_only_the_deployed_result_object(self):
        for app in self.apps:
            with (
                self.subTest(module=app.__name__),
                closing(boto3.Session(region_name=app.AWS_REGION).client("s3")) as client,
            ):
                params = {
                    "Bucket": "example-results",
                    "Key": "results.md",
                    "Body": "Walk at 20°C".encode(),
                    "ContentType": "text/markdown; charset=utf-8",
                }
                with Stubber(client) as stub, patch.object(app.boto3, "client", return_value=client):
                    stub.add_response("put_object", {}, params)
                    stub.add_client_error("put_object", "AccessDenied", "private fixture", expected_params=params)
                    self.assertEqual(app.save_activity_plan("Walk at 20°C")["status"], "success")
                    failure = app.save_activity_plan("Walk at 20°C")
                    self.assertEqual(failure["status"], "error")
                    self.assertNotIn("private fixture", str(failure))
                    stub.assert_no_pending_responses()
                with patch.object(app.boto3, "client", side_effect=AssertionError("unexpected client")):
                    self.assertEqual(app.save_activity_plan(" ")["status"], "error")

    def test_browser_releases_owned_session_on_success_and_failure(self):
        for app in self.apps:
            for failure in (False, True):
                with self.subTest(module=app.__name__, failure=failure), ExitStack() as stack:
                    service = stack.enter_context(patch.object(app, "BrowserClient")).return_value
                    service.generate_ws_headers.return_value = ("wss://example.invalid", {"Authorization": "fixture"})
                    stop = stack.enter_context(patch.object(app.BrowserSession, "stop", new_callable=AsyncMock))
                    session = stack.enter_context(patch.object(app, "BrowserSession", wraps=app.BrowserSession))
                    agent = stack.enter_context(patch.object(app, "BrowserAgent"))
                    hist = Mock()
                    hist.is_successful.return_value = True
                    hist.final_result.return_value = "forecast"
                    agent.return_value.run = AsyncMock(
                        return_value=hist, side_effect=ValueError("private fixture") if failure else None
                    )
                    result = asyncio.run(app.get_weather_data("Richmond"))
                    self.assertEqual(result["status"], "error" if failure else "success")
                    self.assertNotIn("private fixture", str(result))
                    self.assertEqual(session.call_args.kwargs["headers"], {"Authorization": "fixture"})
                    stop.assert_awaited_once()
                    service.stop.assert_called_once_with()

    def test_interpreter_stops_after_empty_or_failed_stream(self):
        for app in self.apps:
            with patch.object(app, "CodeInterpreter") as factory:
                service = factory.return_value
                service.invoke.return_value = {"stream": []}
                self.assertEqual(app.execute_code("pass")["status"], "error")
                service.stop.assert_called_once_with()
                service.invoke.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "InvokeCodeInterpreter")
                self.assertEqual(app.execute_code("pass")["status"], "error")
                self.assertEqual(service.stop.call_count, 2)


if __name__ == "__main__":
    unittest.main()
