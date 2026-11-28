"""Offline trace-parent regression; no AWS, MCP, Langfuse API or model calls."""

import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from botocore.client import BaseClient
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class TraceContextTests(unittest.TestCase):
    def test_parent_link_and_environment_routing_with_real_sdk(self):
        source = Path(__file__).resolve().parents[1] / "agents/strands_claude.py"
        trace_id = "1234567890abcdef1234567890abcdef"
        parent_id = "1234567890abcdef"
        provider = TracerProvider()
        exported = InMemorySpanExporter()
        unfiltered = InMemorySpanExporter()
        env = {
            "AWS_ACCESS_KEY_ID": "offline",
            "AWS_SECRET_ACCESS_KEY": "offline",
            "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_CONFIG_FILE": "/dev/null",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(BaseClient, "_make_api_call", side_effect=AssertionError("AWS forbidden")),
            patch.object(httpx.Client, "send", side_effect=AssertionError("HTTP forbidden")) as http,
            patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("Async HTTP forbidden")) as async_http,
        ):
            spec = importlib.util.spec_from_file_location("trace_runtime_under_test", source)
            runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runtime)
            client = Langfuse(
                public_key="pk-offline-test",
                secret_key="sk-offline-test",
                base_url="http://127.0.0.1:9",
                tracer_provider=provider,
                span_exporter=exported,
                flush_at=1,
                flush_interval=0.1,
                sample_rate=1.0,
            )
            # Langfuse filters non-Langfuse scopes; separately capture the model
            # fixture's span to verify parent context without external export.
            provider.add_span_processor(SimpleSpanProcessor(unfiltered))

            def model_result(prompt):
                self.assertEqual(prompt, "offline question")
                with provider.get_tracer("offline-model-fixture").start_as_current_span("fixture-model"):
                    return SimpleNamespace(message={"content": [{"text": "offline answer"}]})

            mcp = MagicMock()
            mcp.__enter__.return_value.list_tools_sync.return_value = []
            try:
                for environment in ["DEV", "TST", "PROD"]:
                    with self.subTest(environment=environment):
                        exported.clear()
                        unfiltered.clear()
                        runtime.env = environment
                        with (
                            patch.object(runtime, "MCPClient", return_value=mcp) as create_mcp,
                            patch.object(runtime, "Agent", return_value=model_result),
                            patch.object(runtime, "get_client", return_value=client) as get_client,
                        ):
                            result = runtime.strands_agent_bedrock(
                                {"prompt": "offline question", "trace_id": trace_id, "parent_obs_id": parent_id}
                            )
                            self.assertEqual(result, "offline answer")
                            create_mcp.assert_called_once_with(url="https://langfuse.com/api/mcp")
                            provider.force_flush()
                            spans = unfiltered.get_finished_spans()
                            observations = [span for span in spans if span.name == "strands-agent"]
                            if environment in ["DEV", "TST"]:
                                get_client.assert_called_once_with()
                                self.assertEqual(len(observations), 1)
                                observation = observations[0]
                                self.assertEqual(observation.context.trace_id, int(trace_id, 16))
                                self.assertEqual(observation.parent.span_id, int(parent_id, 16))
                                child = next(span for span in spans if span.name == "fixture-model")
                                self.assertEqual(child.parent.span_id, observation.context.span_id)
                            else:
                                get_client.assert_not_called()
                                self.assertEqual(observations, [])
            finally:
                client.shutdown()
                provider.shutdown()
            self.assertEqual(http.call_count, 0)
            self.assertEqual(async_http.call_count, 0)


if __name__ == "__main__":
    unittest.main()
