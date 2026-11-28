"""DATA-O2: genuine SDK no-spans / parsed Evaluate results, then actual main.

Only transport, span collection and inference boundaries are fixtures. No AWS calls.
"""

import copy
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import bedrock_agentcore.evaluation as sdk
import bedrock_agentcore.evaluation.client as client_module

# Imports the maintained pre-import network denial and genuine serializer/parser.
import test_evaluation_contracts as contracts
from test_evaluation_contracts import DATASET_ID, IDS, E, Wire, dataset_client, details


class SessionEvaluationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        batch = contracts.BatchContracts("test_complete_genuine_runner_and_api_reference_conversion")
        cls.good_batch, _ = batch.run_real_runner()
        E.require_complete_batch(cls.good_batch)

    def session(self, mode):
        cp, dp = Wire("bedrock-agentcore-control"), Wire("bedrock-agentcore")
        cp.handlers["ListDatasetExamples"] = {
            "examples": [sdk.PredefinedScenario.model_validate(s).model_dump(mode="json") for s in E.DATASET_SCENARIOS]
        }
        ec = sdk.EvaluationClient(region_name="us-west-2")
        ec._dp_client = dp.client
        ec._evaluator_level_cache = {i: ("TRACE" if i == IDS[2] else "SESSION") for i in IDS}
        index = [0]

        def evaluate(body):
            i = index[0]
            index[0] += 1
            requested = IDS[i]
            arn = (
                "arn:aws:bedrock-agentcore:::evaluator/" + requested if requested.startswith("Builtin.") else requested
            )
            session_id = body["evaluationInput"]["sessionSpans"][0]["attributes"]["session.id"]
            row = {
                "evaluatorArn": arn,
                "evaluatorId": arn.rsplit("/", 1)[-1],
                "evaluatorName": arn.rsplit("/", 1)[-1].split(".")[-1],
                "context": {"spanContext": {"sessionId": session_id, "traceId": "a" * 32}},
                "value": 0 if mode in ("zero", "two-traces") else 4,
            }
            # Botocore parsers do not enforce regex/length constraints. Verify
            # every positive identity against the genuine pinned output model.
            shape = (
                dp.client.meta.service_model.operation_model("Evaluate")
                .output_shape.members["evaluationResults"]
                .member
            )
            for field in ("evaluatorArn", "evaluatorId"):
                metadata = shape.members[field].metadata
                value = row[field]
                self.assertGreaterEqual(len(value), metadata.get("min", 0))
                self.assertLessEqual(len(value), metadata.get("max", float("inf")))
                self.assertIsNotNone(re.fullmatch(metadata["pattern"], value), (field, value))
            if mode == "category":
                row.pop("value")
                row["label"] = "Correct"
            if mode == "empty" or (mode == "partial" and i == 3):
                return {"evaluationResults": []}
            if i == 3:
                if mode == "error":
                    row.update(errorCode="INTERNAL_SERVER_ERROR", errorMessage="offline evaluator failure")
                if mode == "duplicate":
                    row.update(evaluatorArn="arn:aws:bedrock-agentcore:::evaluator/" + IDS[0], evaluatorId=IDS[0])
                if mode == "wrong":
                    row.update(
                        evaluatorArn="arn:aws:bedrock-agentcore:::evaluator/Builtin.Helpfulness",
                        evaluatorId="Builtin.Helpfulness",
                    )
                if mode == "wrong-custom":
                    row.update(evaluatorArn=arn.replace("000000000000", "111122223333"))
                if mode == "arn-as-id":
                    row["evaluatorId"] = arn
                if mode == "mismatch":
                    row["evaluatorId"] = IDS[0]
                if mode == "missing-id":
                    row.pop("evaluatorId")
                if mode == "missing-arn":
                    row.pop("evaluatorArn")
                if mode == "unscored":
                    row.pop("value")
            rows = [row]
            if mode in ("two-traces", "dimensions", "duplicate-dimension") and i == 2:
                second = copy.deepcopy(row)
                if mode == "two-traces":
                    second["context"]["spanContext"]["traceId"] = "c" * 32
                if mode == "dimensions":
                    second["evaluationDimensionName"] = "other"
                if mode == "duplicate-dimension":
                    row["evaluationDimensionName"] = second["evaluationDimensionName"] = "same"
                rows.append(second)
            return {"evaluationResults": rows}

        dp.handlers["Evaluate"] = evaluate

        class Collector:
            def __init__(self, **kwargs):
                pass

            def collect(self, **kwargs):
                if mode == "no-spans":
                    return []
                return [
                    {"traceId": trace * 32, "spanId": trace * 16, "attributes": {"session.id": kwargs["session_id"]}}
                    for trace in (("a", "c") if mode == "two-traces" else ("a",))
                ]

        with (
            patch.object(sdk, "DatasetClient", return_value=dataset_client(cp)),
            patch.object(sdk, "EvaluationClient", return_value=ec),
            patch.object(client_module, "CloudWatchAgentSpanCollector", Collector),
            patch.object(E, "invoke_agent", return_value="offline answer"),
            patch.object(E.time, "sleep"),
        ):
            result = E.run_session_evaluation(
                "us-west-2", "arn:offline/runtime/test", DATASET_ID, "1", "offline-token", *IDS[2:]
            )
        self.assertEqual(index[0], 0 if mode == "no-spans" else 4)
        return result

    def main(self, session, *, requested=True, keep=False, deny_cleanup=False):
        cp = Wire("bedrock-agentcore-control")
        owned = {"name": None, "deleted": False}
        with tempfile.TemporaryDirectory() as tmp:
            journal, token = Path(tmp) / "result.json", Path(tmp) / "token"
            token.write_text("offline-token")

            def created(region, record, path):
                owned["name"] = record["dataset_name"]
                record.update(dataset_id=DATASET_ID, dataset_version="1")
                E.save_results(path, record)
                return DATASET_ID, "1"

            def get(_):
                saved = json.loads(journal.read_text())
                if requested:
                    self.assertEqual(saved["session_evaluation"], session)  # durable before gate/cleanup
                if owned["deleted"]:
                    return 404, {"__type": "ResourceNotFoundException", "message": "absent"}
                value = details()
                value["datasetName"] = owned["name"]
                return value

            def delete(_):
                if deny_cleanup:
                    return 403, {"__type": "AccessDeniedException", "message": "denied"}
                owned["deleted"] = True
                return details("DELETING")

            cp.handlers.update(GetDataset=get, DeleteDataset=delete)
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
            if requested:
                args.append("--use-agentcore-evals")
            if keep:
                args.append("--keep-dataset")
            ctrl = MagicMock()
            ctrl.get_agent_runtime.return_value = {"agentRuntimeName": "offline"}
            stdout, failure = io.StringIO(), None
            with (
                patch.object(sys, "argv", args),
                patch.object(E.boto3, "client", return_value=ctrl),
                patch.object(sdk, "DatasetClient", return_value=dataset_client(cp)),
                patch.object(E, "create_managed_dataset", side_effect=created),
                patch.object(E, "run_batch_evaluation", return_value=copy.deepcopy(self.good_batch)),
                patch.object(E, "run_session_evaluation", return_value=session) as run_session,
                redirect_stdout(stdout),
            ):
                try:
                    E.main()
                except Exception as error:
                    failure = error
            self.assertEqual(run_session.call_count, int(requested))
            saved = json.loads(journal.read_text())
            if requested:
                self.assertEqual(saved["session_evaluation"], session)
            self.assertEqual(saved["dataset_id"], DATASET_ID)
            self.assertTrue(all(DATASET_ID in url for _, _, url in cp.requests))
            return failure, saved, stdout.getvalue(), cp.requests

    def test_genuine_no_spans_and_parsed_incomplete_results_fail_actual_main(self):
        for mode in (
            "no-spans",
            "empty",
            "partial",
            "error",
            "duplicate",
            "duplicate-dimension",
            "wrong",
            "wrong-custom",
            "mismatch",
            "arn-as-id",
            "missing-id",
            "missing-arn",
            "unscored",
        ):
            with self.subTest(mode=mode):
                result = self.session(mode)
                failure, saved, stdout, calls = self.main(result)
                self.assertIsInstance(failure, RuntimeError)
                self.assertNotIn("EVALUATION COMPLETE", stdout)
                self.assertEqual(saved["cleanup"], "verified_absent")
                self.assertEqual(sum(op == "DeleteDataset" for op, _, _ in calls), 1)

    def test_complete_real_results_zero_scores_and_categories_pass(self):
        for mode in ("complete", "zero", "category", "two-traces", "dimensions"):
            with self.subTest(mode=mode):
                result = self.session(mode)
                failure, saved, stdout, _ = self.main(result)
                self.assertIsNone(failure)
                self.assertIn("EVALUATION COMPLETE", stdout)
                self.assertEqual(saved["cleanup"], "verified_absent")
                if mode == "two-traces":
                    self.assertEqual(len(result["results"]), 5)
                if mode == "zero":
                    self.assertEqual([r["value"] for r in saved["session_evaluation"]["results"]], [0] * 4)

    def test_requested_failure_preserves_keep_and_original_plus_cleanup_errors(self):
        result = self.session("no-spans")
        failure, saved, stdout, calls = self.main(result, keep=True)
        self.assertIsInstance(failure, RuntimeError)
        self.assertEqual(saved["cleanup"], "kept")
        self.assertEqual(calls, [])
        self.assertNotIn("EVALUATION COMPLETE", stdout)
        failure, saved, stdout, _ = self.main(result, deny_cleanup=True)
        self.assertIsInstance(failure, ExceptionGroup)
        self.assertIsInstance(failure.exceptions[0], RuntimeError)
        self.assertEqual(failure.exceptions[1].response["Error"]["Code"], "AccessDeniedException")
        self.assertEqual(saved["cleanup"]["status"], "incomplete")
        self.assertNotIn("EVALUATION COMPLETE", stdout)

    def test_unrequested_session_is_not_a_success_requirement(self):
        failure, saved, stdout, _ = self.main(None, requested=False)
        self.assertIsNone(failure)
        self.assertNotIn("session_evaluation", saved)
        self.assertIn("EVALUATION COMPLETE", stdout)


if __name__ == "__main__":
    unittest.main()
