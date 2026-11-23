import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import boto3
from botocore.stub import ANY, Stubber
from preview import collect, start, verified_engine

ACCOUNT, REGION = "111111111111", "us-east-1"
BASE = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:"
G, E, J = "gateway-1234567890", "insurancePolicyEngine-1234567890", "generation-1234567890"
ARN, ENGINE = BASE + "gateway/" + G, BASE + "policy-engine/" + E
STAMP = datetime.now(UTC)
PARAMS = {"policyEngineId": E, "policyGenerationId": J}
JOB = {
    **PARAMS,
    "name": "preview",
    "policyGenerationArn": ENGINE + "/policy-generation/" + J,
    "resource": {"arn": ARN},
    "createdAt": STAMP,
    "updatedAt": STAMP,
    "status": "GENERATED",
    "statusReasons": [],
}


class Preview(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client(
            "bedrock-agentcore-control",
            region_name=REGION,
            aws_access_key_id="fixture",
            aws_secret_access_key="fixture",
        )
        self.stub = self.enterContext(Stubber(self.client))
        self.addCleanup(self.stub.assert_no_pending_responses)

    def preflight(self, attached):
        self.stub.add_response(
            "get_gateway",
            {
                "gatewayId": G,
                "gatewayArn": ARN,
                "createdAt": STAMP,
                "updatedAt": STAMP,
                "status": "READY",
                "name": "fixture",
                "authorizerType": "AWS_IAM",
                "policyEngineConfiguration": {"arn": attached, "mode": "ENFORCE"},
            },
            {"gatewayIdentifier": G},
        )
        self.stub.add_response(
            "get_policy_engine",
            {
                "policyEngineId": E,
                "name": "fixture",
                "policyEngineArn": ENGINE,
                "createdAt": STAMP,
                "updatedAt": STAMP,
                "status": "ACTIVE",
                "statusReasons": [],
            },
            {"policyEngineId": E},
        )

    def test_generation_and_findings(self):
        self.preflight(ENGINE)
        self.assertEqual(verified_engine(self.client, ACCOUNT, REGION, G, E), ARN)
        self.stub.add_response(
            "start_policy_generation",
            JOB,
            {
                "policyEngineId": E,
                "name": ANY,
                "clientToken": ANY,
                "resource": {"arn": ARN},
                "content": {"rawText": "Allow the review tool"},
            },
        )
        self.assertEqual(start(self.client, E, ARN, "Allow the review tool"), J)
        self.stub.add_response("get_policy_generation", {**JOB, "status": "GENERATING"}, PARAMS)
        self.stub.add_response("get_policy_generation", {**JOB, "findings": "Review required"}, PARAMS)
        asset = {
            "policyGenerationAssetId": "asset-1234567890",
            "rawTextFragment": "review",
            "definition": {
                "cedar": {"statement": 'forbid(principal, action == AgentCore::Action::"Fixture", resource);'}
            },
            "findings": [{"type": "WARNING", "description": "Review"}],
        }
        self.stub.add_response("list_policy_generation_assets", {"nextToken": "page2"}, PARAMS)
        self.stub.add_response(
            "list_policy_generation_assets", {"policyGenerationAssets": [asset]}, {**PARAMS, "nextToken": "page2"}
        )
        with patch("preview.time.sleep"):
            result = collect(self.client, E, ARN, J)
        self.assertEqual(result["findings"], "Review required")
        self.assertEqual(result["drafts"][0]["findings"], asset["findings"])
        self.assertEqual(result["drafts"][0]["statement"], asset["definition"]["cedar"]["statement"])

    def test_reject_other_attachment(self):
        self.preflight(ENGINE.replace("insurance", "another"))
        with self.assertRaises(ValueError):
            verified_engine(self.client, ACCOUNT, REGION, G, E)

    def test_generation_failure(self):
        self.stub.add_response("get_policy_generation", {**JOB, "status": "GENERATE_FAILED"}, PARAMS)
        with self.assertRaises(RuntimeError):
            collect(self.client, E, ARN, J)

    def test_resume_wrong_gateway(self):
        self.stub.add_response("get_policy_generation", JOB, PARAMS)
        with self.assertRaises(ValueError):
            collect(self.client, E, ARN + "other", J)

    def test_timeout(self):
        with patch("preview.time.monotonic", side_effect=[0, 301]), self.assertRaises(TimeoutError):
            collect(self.client, E, ARN, J)

    def test_empty_intent(self):
        with self.assertRaises(ValueError):
            start(self.client, E, ARN, " ")
