"""Offline pipeline: real orchestration, Lambda handlers, SDK result mapping and Cedar.

Agents are scripted, JWT issuer/Gateway/DynamoDB are fixtures. No assertion here
claims AWS accepted auth, invoked a Lambda, or persisted a record in DynamoDB.
"""

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.types import CallToolResult, TextContent
from strands.tools.mcp import MCPClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/claimsagent"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineApp:
    logger = logging.getLogger("offline-claims")

    def entrypoint(self, function):
        return function


class GatewayFixture:
    """Uses production Cedar and Lambda code, with in-memory effects and local JWTs."""

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def __init__(self, *, token_claims=None, failure=None):
        claims = {
            "iss": "https://issuer.example.invalid",
            "client_id": "offlineclient",
            "scope": "agentcore/invoke",
            "token_use": "access",
            "exp": int(time.time()) + 300,
        }
        claims.update(token_claims or {})
        self.token = jwt.encode(claims, self.key, algorithm="RS256")
        self.failure = failure
        self.calls, self.claims, self.reviews, self.notifications = [], [], [], []
        self.mapper = MCPClient(lambda: None)  # Constructor only: no transport/start.
        self.handlers = {}
        for tool, directory, effects in (
            ("create_claim", "create_claim", self.claims),
            ("request_human_review", "human_review", self.reviews),
            ("send_notification", "notification", self.notifications),
        ):
            with patch("boto3.resource"), patch("boto3.client"):
                module = load("offline_" + directory, ROOT / f"lambdas/{directory}/handler.py")
            if hasattr(module, "TOPIC_ARN"):
                module.TOPIC_ARN = "arn:aws:sns:us-west-2:111122223333:offline"
            if hasattr(module, "SENDER"):
                module.SENDER = "sender@example.invalid"
            if hasattr(module, "table"):
                module.table.put_item.side_effect = lambda *, Item, rows=effects: rows.append(Item)
            self.handlers[tool] = module

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def authenticate(self):
        claims = jwt.decode(
            self.token, self.key.public_key(), algorithms=["RS256"], issuer="https://issuer.example.invalid"
        )
        if claims["client_id"] != "offlineclient" or claims["token_use"] != "access":
            raise ValueError("JWT client/type rejected")
        return claims

    async def call_tool_async(self, *, tool_use_id, name, arguments):
        self.calls.append({"name": name, "arguments": arguments})
        try:
            self.authenticate()
            if self.failure:
                raise ValueError(self.failure)
            result = await asyncio.to_thread(
                subprocess.run,
                ["node", "--require", str(ROOT / "tests/offline.cjs"), str(ROOT / "tests/cedar_harness.cjs")],
                input=json.dumps({"name": name, "arguments": arguments}),
                capture_output=True,
                text=True,
                check=True,
            )
            decision = json.loads(result.stdout)
            if decision["decision"] != "allow":
                raise ValueError("Cedar forbid: " + str(decision["diagnostics"]["reason"]))
            target, tool = name.split("___")
            expected = {
                "create_claim": "create-claim",
                "request_human_review": "human-review",
                "send_notification": "notification",
            }
            if expected[tool] != target:
                raise ValueError("Unknown tool")
            with patch.dict(os.environ, {"NOTIFICATIONS_ENABLED": "false"}):
                body = self.handlers[tool].handler(arguments, None)
            if tool == "send_notification":
                self.notifications.append(json.loads(body))
            wire = CallToolResult(content=[TextContent(type="text", text=body)], isError=False)
        except (ValueError, jwt.PyJWTError) as error:
            wire = CallToolResult(content=[TextContent(type="text", text=str(error))], isError=True)
        return self.mapper._handle_tool_result(tool_use_id, wire)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth_options = []

        def token_decorator(**options):
            cls.auth_options.append(options)
            return lambda function: function

        with (
            patch("bedrock_agentcore.runtime.BedrockAgentCoreApp", OfflineApp),
            patch("bedrock_agentcore.identity.auth.requires_access_token", token_decorator),
        ):
            cls.agent = load("offline_claims", ROOT / "app/claimsagent/main.py")

    def setUp(self):
        self.agent.reset_state()

    async def run_claim(
        self,
        decision="ACCEPT",
        confidence=92,
        validation=True,
        memory_error=False,
        amount=2000,
        gateway=None,
        claimant_email="test@example.invalid",
    ):
        agent = self.agent

        async def processor_stream(prompt):
            if decision is not None:
                agent.submit_decision(decision, amount, "POL-12345", "auto", "damage", "verified", "active")
            yield {"data": "DECISION: ACCEPT (text alone must never authorize approval)"}

        async def validator_stream(prompt):
            if validation:
                agent.submit_validation(confidence, "AUTO_APPROVE", "reviewed", "None")
            yield {"data": "CONFIDENCE: 100 ROUTING: AUTO_APPROVE"}

        processor, validator = MagicMock(), MagicMock()
        processor.stream_async.side_effect = processor_stream
        validator.stream_async.side_effect = validator_stream
        gateway = gateway or GatewayFixture()
        with (
            patch.object(agent, "get_processor", return_value=processor) as make_processor,
            patch.object(agent, "get_validator", return_value=validator) as make_validator,
            patch.object(agent, "get_mcp_client", return_value=gateway),
            patch.object(
                agent,
                "get_memory_session_manager",
                return_value=None,
                side_effect=RuntimeError("offline memory unavailable") if memory_error else None,
            ) as memory,
            patch.object(agent, "Agent", side_effect=AssertionError("Unexpected third LLM agent")),
        ):
            output = "".join(
                [
                    part
                    async for part in agent.invoke({"prompt": "offline claim", "claimant_email": claimant_email}, None)
                ]
            )
        gateway.memory_ids = memory.call_args.args
        gateway.processor_prompt = processor.stream_async.call_args.args[0]
        make_processor.assert_called_once()
        make_validator.assert_called_once()
        processor.stream_async.assert_called_once()
        validator.stream_async.assert_called_once()
        return output, gateway

    async def test_realistic_email_is_separate_from_memory_ids(self):
        from memory.identifiers import claimant_memory_ids

        for email in ("claimant@example.test", "long.claimant+" * 200 + "@example.test"):
            _, gateway = await self.run_claim(claimant_email=email)
            session_id, actor = gateway.memory_ids
            self.assertEqual(actor, claimant_memory_ids(email)[0])
            self.assertEqual(len(session_id), 38)
            self.assertIn(email, gateway.processor_prompt)
            notifications = [c for c in gateway.calls if c["name"] == "notification___send_notification"]
            self.assertEqual(notifications[0]["arguments"]["recipient_email"], email)

    async def test_auto_approval_has_no_third_llm_and_confirms_write(self):
        output, gateway = await self.run_claim()
        self.assertIn(gateway.claims[0]["claim_id"], output)
        self.assertEqual(gateway.claims[0]["status"], "approved")
        self.assertEqual(gateway.notifications[0]["status"], "suppressed")
        self.assertEqual(len(gateway.claims), 1)
        gateway.handlers["send_notification"].ses.send_email.assert_not_called()

    async def test_low_confidence_forces_human_review_with_same_claim_id(self):
        _, gateway = await self.run_claim(confidence=79)
        self.assertEqual(gateway.claims[0]["status"], "pending_review")
        self.assertEqual(gateway.reviews[0]["claim_id"], gateway.claims[0]["claim_id"])
        gateway.handlers["request_human_review"].sns.publish.assert_not_called()

    async def test_reject_wins_over_validator(self):
        _, gateway = await self.run_claim(decision="REJECT")
        self.assertEqual(gateway.claims, [])
        self.assertEqual(len(gateway.notifications), 1)

    async def test_missing_decision_rejects_even_if_prose_accepts(self):
        _, gateway = await self.run_claim(decision=None)
        self.assertEqual(gateway.claims, [])

    async def test_missing_validation_cannot_auto_approve(self):
        _, gateway = await self.run_claim(validation=False)
        self.assertEqual(gateway.claims[0]["status"], "pending_review")

    async def test_memory_failure_does_not_drop_workflow(self):
        _, gateway = await self.run_claim(memory_error=True)
        self.assertEqual(len(gateway.claims), 1)

    async def test_cedar_boundary_denial_has_no_write_or_notification(self):
        _, allowed = await self.run_claim(amount=99999)
        self.assertEqual(len(allowed.claims), 1)
        for amount in (100000, 150000):
            for confidence in (79, 92):
                with self.subTest(amount=amount, confidence=confidence):
                    output, denied = await self.run_claim(amount=amount, confidence=confidence)
                    self.assertEqual((denied.claims, denied.reviews, denied.notifications), ([], [], []))
                    self.assertEqual(len(denied.calls), 1)
                    self.assertIn("Could not create claim record", output)
                    self.assertNotIn("Claim created:", output)

    async def test_jwt_and_tool_failures_cannot_claim_effects(self):
        for claims in ({"client_id": "wrong"}, {"iss": "https://wrong.invalid"}, {"exp": 1}, {"token_use": "id"}):
            _, gateway = await self.run_claim(gateway=GatewayFixture(token_claims=claims))
            self.assertEqual((gateway.claims, gateway.notifications), ([], []))
        _, failed = await self.run_claim(gateway=GatewayFixture(failure="Lambda error"))
        self.assertEqual((failed.claims, failed.notifications), ([], []))

    def test_gateway_auth_remains_identity_m2m(self):
        self.assertEqual(
            self.auth_options,
            [
                {
                    "provider_name": "Claimsoffline01_dev_cognito-gateway-m2m",
                    "auth_flow": "M2M",
                    "scopes": ["agentcore/invoke"],
                }
            ],
        )
        with patch.object(self.agent, "MCPClient", side_effect=lambda transport, **_: transport) as factory:
            transport = self.agent._build_mcp_client(access_token="offline-jwt")
        self.assertEqual(factory.call_args.kwargs["tool_filters"], {"allowed": ["policy-lookup___lookup_policy"]})
        with patch.object(self.agent, "streamablehttp_client") as stream:
            transport()
        self.assertEqual(stream.call_args.kwargs["headers"], {"Authorization": "Bearer offline-jwt"})

    def test_notification_enabled_handlers_keep_ses_sns_contracts_with_mocks(self):
        gateway = GatewayFixture()
        with patch.dict(os.environ, {"NOTIFICATIONS_ENABLED": "true"}):
            result = json.loads(
                gateway.handlers["send_notification"].handler(
                    {"recipient_email": "recipient@example.invalid", "subject": "offline", "body": "offline"}, None
                )
            )
            gateway.handlers["request_human_review"].handler(
                {"claim_id": "CLM-OFFLINE", "reason": "offline", "estimated_amount": 2000}, None
            )
        self.assertEqual(result["status"], "sent")  # Mock SES, not a delivered message.
        gateway.handlers["send_notification"].ses.send_email.assert_called_once()
        gateway.handlers["request_human_review"].sns.publish.assert_called_once()

    def test_missing_provider_never_acquires_a_bare_v0_token(self):
        with (
            patch.object(self.agent, "GATEWAY_CREDENTIAL_PROVIDER", ""),
            patch.object(self.agent, "_build_mcp_client") as build,
        ):
            self.assertIsNone(self.agent.get_mcp_client())
        build.assert_not_called()

    def test_clients_and_agent_histories_are_not_cached_across_claims(self):
        first, second = object(), object()
        with patch.object(self.agent, "_build_mcp_client", side_effect=[first, second]) as build:
            self.assertIs(self.agent.get_mcp_client(), first)
            self.assertIs(self.agent.get_mcp_client(), second)
        self.assertEqual(build.call_count, 2)
        with (
            patch.object(self.agent, "Agent") as factory,
            patch.object(self.agent, "load_model"),
            patch.object(self.agent, "get_mcp_client", return_value=None),
        ):
            self.agent.get_processor()
            self.agent.get_processor()
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(factory.call_args.kwargs["tools"], [self.agent.submit_decision])

    def test_models_and_validator_tool_isolation(self):
        self.agent._processor = self.agent._validator = None
        with patch.object(self.agent, "BedrockModel") as model:
            self.agent.load_model()
            self.agent.load_model(fast=True)
        self.assertEqual(
            [call.kwargs["model_id"] for call in model.call_args_list],
            ["global.anthropic.claude-sonnet-4-6", "us.anthropic.claude-haiku-4-5-20251001-v1:0"],
        )
        with patch.object(self.agent, "Agent") as factory, patch.object(self.agent, "load_model"):
            self.agent.get_validator()
        self.assertEqual(factory.call_args.kwargs["tools"], [self.agent.submit_validation])
