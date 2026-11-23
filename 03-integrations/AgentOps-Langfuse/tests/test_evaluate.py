import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from evaluate import DATA, coverage, destination, experiment, invoke, runtime_region
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ARN = "arn:aws:bedrock-agentcore:us-west-2:111111111111:runtime/docagent-0123456789"
ITEM = {
    "input": {"question": "Where are the docs?"},
    "expected_output": {"response_facts": ["example docs", "not present"]},
}


class EvaluationTests(unittest.TestCase):
    def test_experiment_runs_real_task_and_evaluator(self):
        provider, exporter = TracerProvider(), InMemorySpanExporter()
        client = Langfuse(
            public_key="pk-sample-experiment",
            secret_key="fixture",
            base_url="https://example.invalid",
            tracer_provider=provider,
            span_exporter=exporter,
            flush_at=1,
            sample_rate=1.0,
        )
        self.addCleanup(client.shutdown)
        runtime = Mock(spec=["invoke_agent_runtime"])
        stream = io.BytesIO(json.dumps("Read the example DOCS").encode())
        runtime.invoke_agent_runtime.return_value = {"contentType": "application/json", "response": stream}
        with (
            patch.object(client, "_get_project_id", return_value="fixture"),
            patch.object(client, "create_score") as score,
        ):
            self.assertEqual(experiment(client, runtime, ARN, [ITEM]), [0.5])
            score.assert_called_once()
        args = runtime.invoke_agent_runtime.call_args.kwargs
        payload = json.loads(args["payload"])
        self.assertEqual(args["agentRuntimeArn"], ARN)
        self.assertEqual(payload["prompt"], ITEM["input"]["question"])
        self.assertTrue(
            any(
                format(s.context.span_id, "016x") == payload["parent_obs_id"]
                and format(s.context.trace_id, "032x") == payload["trace_id"]
                for s in exporter.get_finished_spans()
            )
        )
        self.assertTrue(stream.closed)

    def test_bad_response_is_closed(self):
        for body, content in [(b"{}", "application/json"), (b"not json", "text/plain"), (b'""', "application/json")]:
            stream = io.BytesIO(body)
            with self.assertRaises(ValueError):
                invoke(
                    Mock(invoke_agent_runtime=Mock(return_value={"response": stream, "contentType": content})),
                    ARN,
                    "fixture",
                    "a" * 32,
                    "b" * 16,
                )
            self.assertTrue(stream.closed)

    def test_fail_on_partial_runs(self):
        for results in [[], [SimpleNamespace(output="ok", evaluations=[])]]:
            client = Mock(run_experiment=Mock(return_value=SimpleNamespace(item_results=results)))
            with self.assertRaises(RuntimeError):
                experiment(client, Mock(), ARN, [ITEM])
        for items in [[], [ITEM] * 5]:
            with self.assertRaises(ValueError):
                experiment(Mock(), Mock(), ARN, items)

    def test_destinations_and_references(self):
        self.assertEqual(runtime_region(ARN, "111111111111"), "us-west-2")
        with self.assertRaises(ValueError):
            runtime_region(ARN, "222222222222")
        for url in ["http://example.org", "https://user:pass@example.org", "https://example.org?token=fixture"]:
            with self.assertRaises(ValueError):
                destination(url)
        self.assertEqual(destination("https://example.org/"), "https://example.org")
        for item in json.loads(DATA.read_text()):
            self.assertEqual(
                coverage(
                    output=" ".join(item["expected_output"]["response_facts"]), expected_output=item["expected_output"]
                ).value,
                1,
            )
