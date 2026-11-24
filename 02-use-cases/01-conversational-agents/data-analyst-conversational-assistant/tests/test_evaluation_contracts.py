"""Pinned SDK models/providers/executor and real botocore serializer/parser contracts.

All wire bodies below are OFFLINE fixtures, not evidence of inner service-schema
acceptance. The PREDEFINED payload is produced and consumed by SDK 1.27.0 itself.
"""

import asyncio
import copy
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.update(
    AWS_ACCESS_KEY_ID="offline",
    AWS_SECRET_ACCESS_KEY="offline",
    AWS_CONFIG_FILE="/dev/null",
    AWS_SHARED_CREDENTIALS_FILE="/dev/null",
    AWS_EC2_METADATA_DISABLED="true",
    AWS_REGION="us-west-2",
    AWS_DEFAULT_REGION="us-west-2",
    QUESTION_ANSWERS_TABLE="offline",
)
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Network denied"))
socket.create_connection = socket.socket.connect

from datetime import UTC

import boto3
from bedrock_agentcore.evaluation import DatasetClient, PredefinedScenario, ReferenceInputs
from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_models import CloudWatchDataSourceConfig
from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_runner import BatchEvaluationRunner
from bedrock_agentcore.evaluation.runner.dataset_providers import DatasetManagementServiceProvider
from bedrock_agentcore.evaluation.runner.invoker_types import AgentInvokerOutput
from bedrock_agentcore.evaluation.runner.scenario_executor import PredefinedScenarioExecutor
from botocore.awsrequest import AWSResponse
from botocore.parsers import create_parser

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "cdk-data-analyst-assistant-agentcore-strands/data-analyst-assistant-agentcore-strands"
sys.path.insert(0, str(AGENT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


E = load("evaluation_contract_subject", ROOT / "evaluations/evaluate.py")
DATASET_ID = "data_analyst_eval_fixture-1234567890"
DATASET_NAME = "data_analyst_eval_fixture"
IDS = [
    "Builtin.Correctness",
    "Builtin.GoalSuccessRate",
    "arn:aws:bedrock-agentcore:us-west-2:000000000000:evaluator/SqlAccuracy_offline-1234567890",
    "arn:aws:bedrock-agentcore:us-west-2:000000000000:evaluator/ResponseQuality_offline-1234567890",
]


class Wire:
    """No HTTP: genuine serialized requests -> modeled parser -> actual SDK client."""

    def __init__(self, service):
        self.client = boto3.client(service, region_name="us-west-2")
        self.client._offline_fixture = True  # permits only this explicit fixture under the outer client guard
        self.handlers = {}
        self.requests = []
        self.client._endpoint.make_request = self.respond

    def respond(self, operation, request):
        raw = request.get("body")
        body = json.loads(raw) if raw else None
        self.requests.append((operation.name, body, request["url"]))
        handler = self.handlers[operation.name]
        value = handler(body) if callable(handler) else handler
        status, payload = value if isinstance(value, tuple) else (200, value)
        headers = {"content-type": "application/json"}
        if status >= 400:
            headers["x-amzn-errortype"] = payload["__type"]
        encoded = json.dumps(payload).encode()
        parsed = create_parser(self.client.meta.service_model.protocol).parse(
            {"status_code": status, "headers": headers, "body": encoded}, operation.output_shape
        )
        return AWSResponse(request["url"], status, headers, io.BytesIO(encoded)), parsed


def dataset_client(wire):
    session = MagicMock()
    session.client.return_value = wire.client
    return DatasetClient(region_name="us-west-2", boto3_session=session)


def details(status="ACTIVE"):
    return {
        "datasetId": DATASET_ID,
        "datasetName": DATASET_NAME,
        "datasetArn": "arn:aws:bedrock-agentcore:us-west-2:000000000000:dataset/" + DATASET_ID,
        "status": status,
        "schemaType": "AGENTCORE_EVALUATION_PREDEFINED_V1",
        "createdAt": "2026-11-19T00:00:00Z",
        "downloadUrl": "https://offline.invalid/dataset",
    }


def summary(count=8):
    return {
        "numberOfSessionsCompleted": count,
        "totalNumberOfSessions": count,
        "numberOfSessionsFailed": 0,
        "numberOfSessionsIgnored": 0,
        "numberOfSessionsInProgress": 0,
        "evaluatorSummaries": [
            {"evaluatorId": name, "totalEvaluated": count, "totalFailed": 0, "statistics": {"averageScore": 4}}
            for name in IDS
        ],
    }


class DatasetContracts(unittest.TestCase):
    def setUp(self):
        self.wire = Wire("bedrock-agentcore-control")
        self.client = dataset_client(self.wire)
        self.wire.handlers.update(
            GetDataset=details(), CreateDataset=details("CREATING"), CreateDatasetVersion=details("UPDATING")
        )

    def test_sdk_create_parsed_list_provider_and_reference_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            record = {"dataset_name": DATASET_NAME}
            with patch("bedrock_agentcore.evaluation.DatasetClient", return_value=self.client):
                self.assertEqual(E.create_managed_dataset("us-west-2", record, path), (DATASET_ID, "1"))
            examples = next(
                body["source"]["inlineExamples"]["examples"]
                for op, body, _ in self.wire.requests
                if op == "CreateDataset"
            )
            self.assertEqual(len(examples), 8)
            self.assertTrue(all(isinstance(a, str) for ex in examples for a in ex["assertions"]))
            self.assertEqual(examples[0]["expected_trajectory"], ["DatabaseToolsTarget___execute_sql_query"])
            # Do not simply echo source dictionaries: SDK models are serialized,
            # passed through the real ListDatasetExamples parser, then reloaded.
            self.wire.handlers["ListDatasetExamples"] = {"examples": examples}
            dataset = E.load_dataset(self.client, DATASET_ID, "1")
            download = MagicMock()
            download.iter_lines.return_value = [json.dumps(ex).encode() for ex in examples]
            with patch("requests.get", return_value=download):
                provided = DatasetManagementServiceProvider(DATASET_ID, "1", self.client).get_dataset()
            self.assertEqual([s.model_dump() for s in dataset.scenarios], [s.model_dump() for s in provided.scenarios])
            runner = BatchEvaluationRunner(region="us-west-2")
            ground_truth = runner._transform_ground_truth(dataset.scenarios[0])
            self.assertEqual(ground_truth["assertions"][0], {"text": examples[0]["assertions"][0]})
            self.assertEqual(ground_truth["expectedTrajectory"]["toolNames"], examples[0]["expected_trajectory"])
            from bedrock_agentcore.evaluation import EvaluationClient

            refs = ReferenceInputs(
                assertions=dataset.scenarios[0].assertions,
                expected_trajectory=dataset.scenarios[0].expected_trajectory,
                expected_response=dataset.scenarios[0].turns[0].expected_response,
            )
            converted = EvaluationClient._build_reference_inputs("session", refs, ["a" * 32])
            self.assertEqual(converted[0]["assertions"][0], {"text": examples[0]["assertions"][0]})
            self.assertEqual(converted[1]["expectedResponse"], {"text": examples[0]["turns"][0]["expected_response"]})
            self.assertEqual(json.loads(path.read_text())["dataset_id"], DATASET_ID)

    def test_session_consumer_preserves_assertions(self):
        self.wire.handlers["ListDatasetExamples"] = {"examples": E.DATASET_SCENARIOS}
        ec = MagicMock()
        ec.run.return_value = [{"evaluatorId": name, "value": 4} for name in IDS]
        with (
            patch("bedrock_agentcore.evaluation.DatasetClient", return_value=self.client),
            patch("bedrock_agentcore.evaluation.EvaluationClient", return_value=ec),
            patch.object(E, "invoke_agent"),
            patch.object(E.time, "sleep"),
        ):
            E.run_session_evaluation("us-west-2", "arn:offline/runtime/test", DATASET_ID, "1", "token", *IDS[2:])
        ref = ec.run.call_args.kwargs["reference_inputs"]
        self.assertEqual(ref.assertions, E.DATASET_SCENARIOS[0]["assertions"])
        self.assertEqual(ref.expected_trajectory, ["DatabaseToolsTarget___execute_sql_query"])
        self.assertEqual(ec.run.call_args.kwargs["evaluator_ids"], IDS)

    def test_missing_scenario_is_not_a_new_expected_subset(self):
        self.wire.handlers["ListDatasetExamples"] = {"examples": E.DATASET_SCENARIOS[:-1]}
        with self.assertRaisesRegex(RuntimeError, "all eight"):
            E.load_dataset(self.client, DATASET_ID, "1")


class BatchContracts(unittest.TestCase):
    def run_real_runner(self, aggregate=None, status="COMPLETED", reject_one=False):
        cp, dp = Wire("bedrock-agentcore-control"), Wire("bedrock-agentcore")
        cp.handlers["ListDatasetExamples"] = {
            "examples": [PredefinedScenario.model_validate(s).model_dump(mode="json") for s in E.DATASET_SCENARIOS]
        }
        dp.handlers["StartBatchEvaluation"] = {
            "batchEvaluationId": "batch-1234567890",
            "batchEvaluationArn": "arn:aws:bedrock-agentcore:us-west-2:000000000000:batch-evaluation/batch-1234567890",
        }
        final = {
            "batchEvaluationId": "batch-1234567890",
            "batchEvaluationName": "offline_batch",
            "status": status,
            "createdAt": "2026-11-19T00:00:00Z",
            "evaluationResults": aggregate if aggregate is not None else summary(),
        }
        dp.handlers["GetBatchEvaluation"] = final
        runner = BatchEvaluationRunner(region="us-west-2")
        runner.data_plane_client = dp.client
        runtime = MagicMock()

        def invoke(**kwargs):
            body = json.loads(kwargs["payload"])
            from request_security import request_ids

            request_ids(body, "verified-actor")  # real generated <scenario>-UUID contract
            self.assertEqual(body["session_id"], kwargs["runtimeSessionId"])
            rejected = reject_one and body["prompt"] == E.DATASET_SCENARIOS[0]["turns"][0]["input"]
            text = {"error": "invocation rejected"} if rejected else {"data": "answer"}
            return {"response": io.BytesIO(("data: " + json.dumps(json.dumps(text)) + "\n\n").encode())}

        runtime.invoke_agent_runtime.side_effect = invoke
        with (
            patch("bedrock_agentcore.evaluation.DatasetClient", return_value=dataset_client(cp)),
            patch("bedrock_agentcore.evaluation.BatchEvaluationRunner", return_value=runner),
            patch.object(CloudWatchDataSourceConfig, "pre_evaluation_run_hook"),
            patch.object(E.boto3, "client", return_value=runtime),
        ):
            result = E.run_batch_evaluation(
                "us-west-2", "arn:offline", DATASET_ID, "1", "log", "service", "token", *IDS[2:]
            )
        self.assertEqual(runtime.invoke_agent_runtime.call_count, 8)
        return result, dp.requests

    def test_complete_genuine_runner_and_api_reference_conversion(self):
        result, requests = self.run_real_runner()
        E.require_complete_batch(result)
        for field in ("expected_scenario_ids", "expected_evaluators"):
            incomplete = copy.deepcopy(result)
            del incomplete[field]
            with self.assertRaises(RuntimeError):
                E.require_complete_batch(incomplete)
        start = next(body for op, body, _ in requests if op == "StartBatchEvaluation")
        self.assertEqual(len(start["evaluationMetadata"]["sessionMetadata"]), 8)
        first = next(
            m
            for m in start["evaluationMetadata"]["sessionMetadata"]
            if m["testScenarioId"] == E.DATASET_SCENARIOS[0]["scenario_id"]
        )
        self.assertEqual(
            first["groundTruth"]["inline"]["expectedTrajectory"],
            {"toolNames": ["DatabaseToolsTarget___execute_sql_query"]},
        )
        self.assertEqual(
            first["groundTruth"]["inline"]["assertions"][0], {"text": E.DATASET_SCENARIOS[0]["assertions"][0]}
        )

    def test_seven_of_eight_plus_invocation_failure_rejected(self):
        result, _requests = self.run_real_runner(summary(7), reject_one=True)
        self.assertEqual(result["completed"], 7)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(result["runner_result"]["agent_invocation_failures"]), 1)
        self.assertEqual(result["total_scenarios"], 8)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            E.require_complete_batch(result)

    def test_main_rejects_seven_of_eight_and_retains_runner_diagnostics(self):
        result, _ = self.run_real_runner(summary(7), reject_one=True)
        with tempfile.TemporaryDirectory() as tmp:
            token, journal = Path(tmp) / "token", Path(tmp) / "result.json"
            token.write_text("offline-token")
            args = [
                "evaluate.py",
                "--region",
                "us-west-2",
                "--agent-runtime-arn",
                "arn:offline/runtime/test",
                "--id-token-file",
                str(token),
                "--results-file",
                str(journal),
                "--sql-evaluator-arn",
                IDS[2],
                "--response-evaluator-arn",
                IDS[3],
            ]

            def create(region, record, path):
                record.update(dataset_id=DATASET_ID, dataset_version="1")
                E.save_results(path, record)
                return DATASET_ID, "1"

            ctrl = MagicMock()
            ctrl.get_agent_runtime.return_value = {"agentRuntimeName": "offline"}
            with (
                patch.object(sys, "argv", args),
                patch.object(E.boto3, "client", return_value=ctrl),
                patch.object(E, "create_managed_dataset", side_effect=create),
                patch.object(E, "run_batch_evaluation", return_value=result),
                patch.object(E, "cleanup_dataset") as cleanup,
                self.assertRaisesRegex(RuntimeError, "incomplete"),
            ):
                E.main()
            saved = json.loads(journal.read_text())
            self.assertEqual(saved["batch_evaluation"]["total_scenarios"], 8)
            self.assertEqual(len(saved["batch_evaluation"]["runner_result"]["agent_invocation_failures"]), 1)
            self.assertEqual(saved["cleanup"], "verified_absent")
            cleanup.assert_called_once_with("us-west-2", DATASET_ID, saved["dataset_name"])

    def test_missing_counters_coverage_and_terminal_failure(self):
        cases = []
        for field in summary():
            value = summary()
            del value[field]
            cases.append(value)
        value = summary()
        value["evaluatorSummaries"] = value["evaluatorSummaries"][:-1]
        cases.append(value)
        value = summary()
        value["evaluatorSummaries"][0]["totalEvaluated"] = 7
        cases.append(value)
        value = summary()
        value["evaluatorSummaries"][0]["totalFailed"] = 1
        cases.append(value)
        for field in ("totalEvaluated", "totalFailed", "evaluatorId"):
            value = summary()
            del value["evaluatorSummaries"][0][field]
            cases.append(value)
        for aggregate in cases:
            with self.subTest(aggregate=aggregate):
                result, _ = self.run_real_runner(aggregate)
                with self.assertRaises(RuntimeError):
                    E.require_complete_batch(result)
        result, _ = self.run_real_runner(status="FAILED")
        with self.assertRaises(RuntimeError):
            E.require_complete_batch(result)


class LifecycleContracts(unittest.TestCase):
    def setUp(self):
        self.wire = Wire("bedrock-agentcore-control")
        self.client = dataset_client(self.wire)
        self.not_found = (404, {"__type": "ResourceNotFoundException", "message": "absent"})
        self.denied = (403, {"__type": "AccessDeniedException", "message": "denied"})
        self.wire.handlers.update(
            CreateDataset=details("CREATING"), GetDataset=details(), DeleteDataset=details("DELETING")
        )

    def test_publication_failure_journals_id_and_keep_or_cleanup(self):
        self.wire.handlers["CreateDatasetVersion"] = self.denied
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("bedrock_agentcore.evaluation.DatasetClient", return_value=self.client),
        ):
            path = Path(tmp) / "run.json"
            record = {
                "format": "data-analyst-evaluation-v1",
                "run_id": "fixture",
                "dataset_name": DATASET_NAME,
                "region": "us-west-2",
            }
            with self.assertRaises(self.wire.client.exceptions.AccessDeniedException) as failed:
                E.create_managed_dataset("us-west-2", record, path)
            self.assertEqual(json.loads(path.read_text())["dataset_id"], DATASET_ID)
            with self.assertRaises(self.wire.client.exceptions.AccessDeniedException):
                E.finish_run(record, path, True, failed.exception)
            self.assertEqual(json.loads(path.read_text())["cleanup"], "kept")
            self.assertNotIn("DeleteDataset", [op for op, _, _ in self.wire.requests])
            self.wire.handlers["DeleteDataset"] = self.denied
            with self.assertRaises(ExceptionGroup) as combined:
                E.finish_run(record, path, False, failed.exception)
            self.assertIs(combined.exception.exceptions[0], failed.exception)
            self.assertEqual(json.loads(path.read_text())["cleanup"]["status"], "incomplete")
            self.wire.handlers["DeleteDataset"] = details("DELETING")
            responses = iter([details(), details("DELETING"), self.not_found])
            self.wire.handlers["GetDataset"] = lambda _: next(responses)
            with patch.object(E.time, "sleep"):
                E.recover_dataset_cleanup(path)
            self.assertEqual(json.loads(path.read_text())["cleanup"], "verified_absent")
            self.assertTrue(
                all(DATASET_ID in url for op, _, url in self.wire.requests if op in ("DeleteDataset", "GetDataset"))
            )

    def test_main_publication_failure_cleans_exact_journaled_id(self):
        owned = {"name": None, "deleted": False}

        def create(body):
            owned["name"] = body["datasetName"]
            value = details("CREATING")
            value["datasetName"] = owned["name"]
            return value

        def get(_):
            if owned["deleted"]:
                return self.not_found
            value = details()
            value["datasetName"] = owned["name"]
            return value

        def delete(_):
            owned["deleted"] = True
            return details("DELETING")

        self.wire.handlers.update(
            CreateDataset=create, GetDataset=get, DeleteDataset=delete, CreateDatasetVersion=self.denied
        )
        with tempfile.TemporaryDirectory() as tmp:
            token, journal = Path(tmp) / "token", Path(tmp) / "journal.json"
            token.write_text("offline-not-a-real-token")
            args = [
                "evaluate.py",
                "--region",
                "us-west-2",
                "--agent-runtime-arn",
                "arn:offline/runtime/test",
                "--id-token-file",
                str(token),
                "--results-file",
                str(journal),
                "--sql-evaluator-arn",
                "arn:aws:bedrock-agentcore:us-west-2:000000000000:evaluator/sql",
                "--response-evaluator-arn",
                "arn:aws:bedrock-agentcore:us-west-2:000000000000:evaluator/quality",
            ]
            ctrl = MagicMock()
            ctrl.get_agent_runtime.return_value = {"agentRuntimeName": "offline"}
            with (
                patch.object(sys, "argv", args),
                patch.object(E.boto3, "client", return_value=ctrl),
                patch("bedrock_agentcore.evaluation.DatasetClient", return_value=self.client),
                self.assertRaises(self.wire.client.exceptions.AccessDeniedException),
            ):
                E.main()
            record = json.loads(journal.read_text())
            self.assertEqual(record["dataset_id"], DATASET_ID)
            self.assertEqual(record["cleanup"], "verified_absent")
            self.assertEqual(record["failure"]["type"], "AccessDeniedException")
            self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
            self.assertEqual([op for op, _, _ in self.wire.requests].count("DeleteDataset"), 1)

    def test_denied_failed_unknown_and_timeout_deletion_are_nonzero(self):
        with patch("bedrock_agentcore.evaluation.DatasetClient", return_value=self.client):
            self.wire.handlers["DeleteDataset"] = self.denied
            with self.assertRaises(self.wire.client.exceptions.AccessDeniedException):
                E.cleanup_dataset("us-west-2", DATASET_ID, DATASET_NAME)
            self.wire.handlers["DeleteDataset"] = details("DELETING")
            for status in ("DELETE_FAILED", "UNRECOGNIZED", None):
                responses = iter([details(), details(status)])
                self.wire.handlers["GetDataset"] = lambda _, answers=responses: next(answers)
                with self.assertRaises(RuntimeError):
                    E.cleanup_dataset("us-west-2", DATASET_ID, DATASET_NAME)
            self.wire.handlers["GetDataset"] = details("DELETING")
            with self.assertRaises(TimeoutError):
                E.wait_for_dataset(self.client, DATASET_ID, deleting=True, timeout=0)
            self.wire.handlers["GetDataset"] = self.denied
            with self.assertRaises(self.wire.client.exceptions.AccessDeniedException):
                E.wait_for_dataset(self.client, DATASET_ID, deleting=True)
            self.wire.handlers["GetDataset"] = self.not_found
            E.cleanup_dataset("us-west-2", DATASET_ID, DATASET_NAME)


class RunnerIdentityContracts(unittest.TestCase):
    def test_real_executor_through_runtime_with_verified_actors(self):
        from datetime import datetime, timezone

        import jwt
        import request_security
        from cryptography.hazmat.primitives.asymmetric import rsa

        app = load("runner_runtime_app", AGENT / "app.py")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        actors = [str(uuid4()), str(uuid4())]
        keys = MagicMock()
        keys.get_signing_key_from_jwt.return_value.key = key.public_key()
        configs, original_ids = [], []

        async def stream(_):
            yield {"data": "offline answer"}

        def manager(**kwargs):
            configs.append(kwargs["agentcore_memory_config"])
            return MagicMock()

        def invoke(invocation):
            original_ids.append(invocation.session_id)
            for actor in actors:
                now = int(datetime.now(UTC).timestamp())
                token = jwt.encode(
                    {
                        "sub": actor,
                        "iss": "https://issuer.invalid",
                        "aud": "client",
                        "token_use": "id",
                        "iat": now,
                        "exp": now + 300,
                    },
                    key,
                    algorithm="RS256",
                )
                payload = {
                    "prompt": invocation.payload,
                    "id_token": token,
                    "session_id": invocation.session_id,
                    "prompt_uuid": str(uuid4()),
                }

                async def collect(request=payload):
                    return [json.loads(c) async for c in app.agent_invocation(request)]

                events = asyncio.run(collect())
                self.assertFalse(any("error" in e for e in events), events)
            return AgentInvokerOutput(agent_output="answer")

        mcp = MagicMock()
        mcp.list_tools_sync.return_value = []
        with (
            patch.object(request_security, "identity_config", return_value=("https://issuer.invalid", "client", keys)),
            patch.object(app, "memory_id", "memory-1234567890"),
            patch.object(app, "BedrockModel"),
            patch.object(app, "AgentCoreMemorySessionManager", side_effect=manager),
            patch.object(app, "get_mcp_client", return_value=mcp),
            patch.object(app, "Agent", return_value=MagicMock(stream_async=stream)),
        ):
            scenario = PredefinedScenario.model_validate(E.DATASET_SCENARIOS[0])
            execution = PredefinedScenarioExecutor(agent_invoker=invoke).run_scenario(scenario)
        self.assertEqual(execution.status, "COMPLETED")
        self.assertEqual(original_ids, [execution.session_id])
        self.assertTrue(execution.session_id.startswith(scenario.scenario_id + "-"))
        self.assertEqual([c.actor_id for c in configs], actors)
        self.assertNotEqual(configs[0].session_id, configs[1].session_id)
        for actor, config in zip(actors, configs):
            self.assertEqual(
                config.session_id,
                request_security.request_ids({"session_id": execution.session_id, "prompt_uuid": str(uuid4())}, actor)[
                    1
                ],
            )
        base = {"session_id": str(uuid4()), "prompt_uuid": str(uuid4())}
        request_security.request_ids(base, actors[0])
        for bad in ("short", "a" * 101, "/" + "a" * 40, "a " * 20, "é" * 40, "a" * 40 + "\n"):
            with self.assertRaises(ValueError):
                request_security.request_ids(dict(base, session_id=bad), actors[0])
        with self.assertRaises(ValueError):
            request_security.request_ids(dict(base, prompt_uuid="invalid"), actors[0])


if __name__ == "__main__":
    unittest.main()
