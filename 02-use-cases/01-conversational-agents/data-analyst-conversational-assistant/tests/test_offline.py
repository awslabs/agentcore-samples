"""Offline contracts using the exact installed SDKs, fake credentials and denied sockets."""

import asyncio
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_SESSION_TOKEN="testing",
    AWS_EC2_METADATA_DISABLED="true",
    AWS_CONFIG_FILE="/nonexistent",
    AWS_SHARED_CREDENTIALS_FILE="/nonexistent",
    AWS_REGION="us-east-1",
    AWS_DEFAULT_REGION="us-east-1",
    QUESTION_ANSWERS_TABLE="offline-results",
)
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Network forbidden"))
socket.create_connection = socket.socket.connect

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "cdk-data-analyst-assistant-agentcore-strands/data-analyst-assistant-agentcore-strands"
sys.path.insert(0, str(AGENT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from datetime import UTC

import httpx
import jwt
from botocore.credentials import Credentials
from cryptography.hazmat.primitives.asymmetric import rsa
from query_results import QueryResults
from request_security import GatewaySigV4, authenticated_actor, request_ids

deploy = load("configure_sample", ROOT / "deploy.py")
db = load("db_tools", ROOT / "lambdas/db_tools/lambda_function.py")
loader = load(
    "data_loader", ROOT / "cdk-data-analyst-assistant-agentcore-strands/resources/lambdas/data-loader/index.py"
)


class ConfigurationTests(unittest.TestCase):
    def test_fresh_v1_and_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "agentcore").mkdir()
            for name in ("agentcore.json", "aws-targets.json"):
                shutil.copyfile(ROOT / "agentcore" / name, root / "agentcore" / name)
            with self.assertRaises(ValueError):
                deploy.configure(root, "DataTest", "000000000000", "us-east-1", False)
            (root / "deploy_outputs.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "Existing state"):
                deploy.configure(root, "DataTest", "000000000000", "us-east-1", True)
            (root / "deploy_outputs.json").unlink()  # test fixture only
            target = root / "agentcore/aws-targets.json"
            target.write_text('{"targets": []}')
            with self.assertRaisesRegex(ValueError, "legacy object"):
                deploy.configure(root, "DataTest", "000000000000", "us-east-1", True)
            target.write_text("[]")
            self.assertEqual(
                deploy.configure(root, "DataTest", "000000000000", "us-east-1", True), "AgentCore-DataTest-dev"
            )
            self.assertEqual(json.loads(target.read_text())[0]["account"], "000000000000")
            self.assertTrue((root / "agentcore/fresh-port.json").exists())
            with self.assertRaises(ValueError):
                deploy.configure(root, "DataTest", "000000000000", "us-east-1", True)


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.actor = str(uuid4())

    def token(self, **changes):
        claims = {
            "sub": self.actor,
            "iss": "https://issuer.invalid",
            "aud": "client",
            "token_use": "id",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        }
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256")

    def verify(self, payload):
        keys = MagicMock()
        keys.get_signing_key_from_jwt.return_value.key = self.key.public_key()
        with patch("request_security.identity_config", return_value=("https://issuer.invalid", "client", keys)):
            return authenticated_actor(payload)

    def test_verified_subject_and_spoofing(self):
        self.assertEqual(self.verify({"id_token": self.token()}), self.actor)
        with self.assertRaises(ValueError):
            self.verify({"id_token": self.token(), "user_id": str(uuid4())})

    def test_wrong_issuer_audience_expiry_and_token_use(self):
        for change in ({"iss": "https://attacker.invalid"}, {"aud": "other"}, {"exp": 1}, {"token_use": "access"}):
            with self.subTest(change=change), self.assertRaises((ValueError, jwt.PyJWTError)):
                self.verify({"id_token": self.token(**change)})
        with self.assertRaises(ValueError):
            self.verify({})
        with self.assertRaises(jwt.PyJWTError):
            self.verify({"id_token": self.token()[:-8] + "invalid!"})

    def test_actor_scoped_sessions_and_valid_ids(self):
        payload = {"session_id": str(uuid4()), "prompt_uuid": str(uuid4())}
        self.assertNotEqual(request_ids(payload, self.actor)[1], request_ids(payload, str(uuid4()))[1])
        with self.assertRaises(ValueError):
            request_ids(dict(payload, prompt_uuid="../someone"), self.actor)

    def test_all_mcp_methods_are_signed_with_refreshed_credentials(self):
        session = MagicMock()
        session.get_credentials.return_value = Credentials("testing", "testing", "session-token")
        auth = GatewaySigV4("us-east-1", session)
        for method in ("POST", "GET", "DELETE"):
            request = httpx.Request(method, "https://gateway.invalid/mcp?x=1", content=b"{}")
            signed = next(auth.auth_flow(request))
            self.assertIn("/us-east-1/bedrock-agentcore/aws4_request", signed.headers["authorization"])
            self.assertEqual(signed.headers["x-amz-security-token"], "session-token")
        self.assertEqual(session.get_credentials.call_count, 3)


class ResultTests(unittest.TestCase):
    def test_post_gateway_hook_and_actor_partition(self):
        client = MagicMock()
        prompt = str(uuid4())
        results = QueryResults("actor-a", prompt, client)
        event = SimpleNamespace(
            tool_use={
                "name": "DatabaseToolsTarget___execute_sql_query",
                "input": {"sql_query": "SELECT 1", "description": "one", "user_id": "actor-b"},
            },
            result={"status": "success", "content": [{"text": '{"result":[{"x":1}]}'}]},
        )
        results.save(event)
        item = client.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["id"]["S"], f"actor-a#{prompt}")
        client.query.return_value = {"Items": [item]}
        self.assertEqual(results.read()[0]["query_results"], [{"x": 1}])
        self.assertEqual(client.query.call_args.kwargs["ExpressionAttributeValues"][":id"]["S"], f"actor-a#{prompt}")
        self.assertNotEqual(QueryResults("actor-b", prompt, client).key, results.key)
        event.result["status"] = "error"
        results.save(event)
        self.assertEqual(client.put_item.call_count, 1)
        event.result = {"status": "success", "content": [{"text": json.dumps({"result": ["x" * 280001]})}]}
        with self.assertRaises(ValueError):
            results.save(event)

    def test_database_denials_nulls_and_size_limit(self):
        client = MagicMock()
        with (
            patch.dict(
                os.environ,
                READONLY_SECRET_ARN="readonly",
                AURORA_RESOURCE_ARN="cluster",
                DATABASE_NAME="db",
                MAX_RESPONSE_SIZE_BYTES="25",
            ),
            patch.object(db.boto3, "client", return_value=client),
        ):
            for sql in (
                "DELETE FROM x",
                "SELECT 1; DROP TABLE x",
                "SELECT email FROM x",
                "SELECT cost_per_unit FROM x",
                "SELECT pg_sleep(10)",
            ):
                self.assertIn("error", db.handle_execute_sql_query({"sql_query": sql}))
            client.execute_statement.assert_not_called()
            client.execute_statement.return_value = {
                "columnMetadata": [{"name": "x"}],
                "records": [[{"isNull": True}], [{"stringValue": "long" * 30}]],
            }
            result = db.handle_execute_sql_query({"sql_query": "SELECT x FROM x"})
            self.assertEqual(result["result"], [{"x": None}])
            self.assertLessEqual(len(json.dumps(result["result"]).encode()), 25)
            self.assertEqual(client.execute_statement.call_args.kwargs["secretArn"], "readonly")

    def test_provider_delete_is_local_and_create_failure_propagates(self):
        self.assertEqual(
            loader.handler({"RequestType": "Delete", "PhysicalResourceId": "stable"}, None),
            {"PhysicalResourceId": "stable"},
        )
        with (
            patch.object(loader, "setup_database", side_effect=ValueError("failed")),
            self.assertRaises(ValueError),
        ):
            loader.handler({"RequestType": "Create"}, None)

    def test_runtime_rejects_missing_identity_before_model_or_memory(self):
        app = load("sample_app", AGENT / "app.py")
        with (
            patch.object(app, "authenticated_actor", side_effect=ValueError("bad identity")),
            patch.object(app, "BedrockModel") as model,
        ):

            async def collect():
                return [json.loads(chunk) async for chunk in app.agent_invocation({"prompt": "hello"})]

            self.assertIn("error", asyncio.run(collect())[0])
            model.assert_not_called()

    def test_real_asgi_server_health_and_rejected_invocation(self):
        module = load("asgi_sample_app", AGENT / "app.py")
        with patch.object(module, "authenticated_actor", side_effect=ValueError("invalid token")):

            async def exercise():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=module.app), base_url="http://offline"
                ) as client:
                    health = await client.get("/ping")
                    self.assertEqual(health.status_code, 200)
                    response = await client.post("/invocations", json={"prompt": "hello"})
                    self.assertEqual(response.status_code, 200)  # streamed application error
                    self.assertIn("Request rejected", response.text)

            asyncio.run(exercise())


class EvaluationTests(unittest.TestCase):
    def test_batch_uses_both_custom_evaluators_and_authenticated_payload(self):
        evaluation = load("sample_evaluation", ROOT / "evaluations/evaluate.py")
        with self.assertRaises(RuntimeError):
            evaluation.decode_runtime_response('data: {"error":"denied"}\n')
        with self.assertRaises(RuntimeError):
            evaluation.decode_runtime_response("")
        import bedrock_agentcore.evaluation as sdk

        dc = MagicMock()
        dc.list_dataset_examples.return_value = {"examples": evaluation.DATASET_SCENARIOS}
        runner = MagicMock()
        from datetime import datetime, timezone

        from bedrock_agentcore.evaluation.runner.batch.batch_evaluation_models import BatchEvaluationResult

        runner.run_dataset_evaluation.return_value = BatchEvaluationResult(
            status="COMPLETED",
            batch_evaluation_id="offline",
            batch_evaluation_name="offline",
            batch_evaluation_arn="arn:offline",
            created_at=datetime.now(UTC),
            agent_invocation_failures=[],
            evaluation_results=None,
            error_details=None,
            output_data_config=None,
        )
        runtime = MagicMock()
        runtime.invoke_agent_runtime.return_value = {
            "response": SimpleNamespace(read=lambda: b'data: "{\\"data\\":\\"answer\\"}"\n\n')
        }
        with (
            patch.object(sdk, "DatasetClient", return_value=dc),
            patch.object(sdk, "BatchEvaluationRunner", return_value=runner),
            patch.object(evaluation.boto3, "client", return_value=runtime),
        ):
            evaluation.run_batch_evaluation(
                "us-east-1",
                "arn:offline",
                "dataset",
                "1",
                "log",
                "service",
                "fake-id-token",
                "custom-sql",
                "custom-quality",
            )
            args = runner.run_dataset_evaluation.call_args.kwargs
            self.assertIn("custom-sql", args["config"].evaluator_config.evaluator_ids)
            self.assertIn("custom-quality", args["config"].evaluator_config.evaluator_ids)
            args["agent_invoker"](SimpleNamespace(payload="question", session_id=str(uuid4())))
            payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
            self.assertEqual(payload["id_token"], "fake-id-token")
            self.assertNotIn("user_id", payload)
            self.assertTrue(payload["prompt_uuid"])


if __name__ == "__main__":
    unittest.main()
