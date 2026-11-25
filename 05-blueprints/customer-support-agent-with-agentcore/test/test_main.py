import asyncio
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cedarpy import is_authorized
from scripts.chat import selected_stack
from src import main
from src.mcp_client.client import get_streamable_http_mcp_client
from starlette.exceptions import HTTPException

ROOT = Path(__file__).resolve().parents[1]
REGION, ACCOUNT = "us-east-1", "111111111111"
STACK = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/supportAgentDemo-AgentCoreStack/12345678-1234-1234-1234-123456789012"


class Contracts(unittest.TestCase):
    def test_actor_only_from_authorized_context(self):
        ctx = SimpleNamespace(session_id="s" * 36, request_headers={})
        with self.assertRaises(HTTPException):
            main.identity(ctx)
        ctx.request_headers["Authorization"] = "Bearer " + jwt.encode(
            {"sub": "synthetic-user"}, "fixture" * 8, algorithm="HS256"
        )
        self.assertEqual(main.identity(ctx)[1:], ("synthetic-user", "s" * 36))

        async def bad():
            async for _ in main.invoke({"prompt": "hello", "user_id": "other"}, ctx):
                pass

        with self.assertRaises(HTTPException):
            asyncio.run(bad())

    def test_fixed_gateway_origin(self):
        with patch.dict(os.environ, {"GATEWAY_URL": "https://example.invalid/mcp"}), self.assertRaises(ValueError):
            get_streamable_http_mcp_client("fixture", REGION)

    def test_stack_identity(self):
        arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/support-1234567890"
        domain = f"supportagentdemo-{ACCOUNT}-{REGION}.auth.{REGION}.amazoncognito.com"
        outputs = [
            {"OutputKey": k, "OutputValue": v}
            for k, v in {"RuntimeArn": arn, "ClientId": "client", "CognitoDomain": domain}.items()
        ]
        stack = Mock(
            describe_stacks=Mock(
                return_value={"Stacks": [{"StackId": STACK, "StackStatus": "CREATE_COMPLETE", "Outputs": outputs}]}
            )
        )
        self.assertEqual(selected_stack(stack, STACK, REGION, ACCOUNT), (domain, "client", arn))
        with self.assertRaises(ValueError):
            selected_stack(stack, STACK, REGION, "222222222222")

    def test_synthesized_cedar(self):
        """Run cdk synth FIRST. Missing templates are failures, not skipped checks."""
        res = json.loads((ROOT / "cdk/cdk.out/supportAgentDemo-AgentCoreStack.template.json").read_text())["Resources"]
        gateway_id, gateway = next((k, v) for k, v in res.items() if v["Type"] == "AWS::BedrockAgentCore::Gateway")
        engine_policies = [
            key
            for key, resource in res.items()
            if resource["Type"] == "AWS::IAM::Policy"
            and any(
                "bedrock-agentcore:GetPolicyEngine" in s["Action"]
                for s in resource["Properties"]["PolicyDocument"]["Statement"]
            )
        ]
        self.assertEqual(len(engine_policies), 1)
        self.assertIn(engine_policies[0], gateway["DependsOn"])
        self.assertEqual(gateway["Properties"]["PolicyEngineConfiguration"]["Mode"], "ENFORCE")
        subjects = []
        for v in res.values():
            if v["Type"] == "AWS::BedrockAgentCore::Policy":
                delim, parts = v["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Join"]
                self.assertEqual(delim, "")
                for part in parts:
                    if not isinstance(part, str):
                        self.assertEqual(part, {"Fn::GetAtt": [gateway_id, "GatewayArn"]})
                subjects.append("".join(p if isinstance(p, str) else "fixture" for p in parts))
        self.assertEqual(len(subjects), 2)
        uid = {"type": "Demo::User", "id": "fixture"}
        for group, cents, allowed in [
            ("standard", 1, True),
            ("standard", 10000, True),
            ("standard", 10001, False),
            ("standard", 0, False),
            ("nonstandard", 1, False),
            ("vip", 10, False),
        ]:
            entity = {"uid": uid, "attrs": {}, "parents": [], "tags": {"cognito:groups": json.dumps([group])}}
            req = {
                "principal": uid,
                "action": {"type": "AgentCore::Action", "id": "supportAgentDemo-OrderTarget___process_refund"},
                "resource": {"type": "AgentCore::Gateway", "id": "fixture"},
                "context": {"input": {"amount_cents": cents}},
            }
            result = is_authorized(req, "\n".join(subjects), [entity])
            self.assertFalse(result.diagnostics.errors)
            self.assertEqual(result.allowed, allowed)
