"""Exercise exactly the Lambda source embedded in the template, with no network."""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import boto3
import yaml
from botocore.stub import Stubber

TEMPLATE = yaml.load(Path(__file__).with_name("inventory.yaml").read_text(), Loader=yaml.BaseLoader)
RES = TEMPLATE["Resources"]
CONTEXT = SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "inventory___get_device"}))


class Inventory(unittest.TestCase):
    def setUp(self):
        module = {}
        exec(compile(RES["Handler"]["Properties"]["Code"]["ZipFile"], "index.py", "exec"), module)  # noqa: S102 -- checked-in Lambda source only
        self.handler = module["handler"]
        client = boto3.client(
            "dynamodb", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )
        self.stub = self.enterContext(Stubber(client))
        self.addCleanup(self.stub.assert_no_pending_responses)
        self.enterContext(patch.dict(os.environ, {"TABLE_NAME": "inventory-fixture"}))
        self.enterContext(patch.object(boto3, "client", return_value=client))

    def test_projection_and_not_found(self):
        request = {
            "TableName": "inventory-fixture",
            "Key": {"device_id": {"S": "demo-01"}},
            "ProjectionExpression": "#id, #model, #fw",
            "ExpressionAttributeNames": {"#id": "device_id", "#model": "model", "#fw": "firmware_version"},
        }
        self.stub.add_response(
            "get_item",
            {"Item": {"device_id": {"S": "demo-01"}, "model": {"S": "router"}, "extra": {"S": "not returned"}}},
            request,
        )
        self.assertEqual(
            self.handler({"device_id": "demo-01"}, CONTEXT),
            {"found": True, "device": {"device_id": "demo-01", "model": "router"}},
        )
        self.stub.add_response("get_item", {}, request)
        self.assertEqual(self.handler({"device_id": "demo-01"}, CONTEXT), {"found": False})

    def test_reject_unknown_tool_and_extra_inputs(self):
        for args in [{}, {"device_id": 9}, {"device_id": ""}, {"device_id": "demo", "action_name": "other"}]:
            with self.assertRaises(ValueError):
                self.handler(args, CONTEXT)
        with self.assertRaises(ValueError):
            self.handler(
                {"device_id": "demo"},
                SimpleNamespace(
                    client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": "other___get_device"})
                ),
            )

    def test_gateway_and_resource_scope(self):
        gateway = RES["Gateway"]["Properties"]
        self.assertEqual(gateway["AuthorizerType"], "CUSTOM_JWT")
        jwt = gateway["AuthorizerConfiguration"]["CustomJWTAuthorizer"]
        self.assertEqual(jwt["AllowedClients"], ["AppClientId"])
        self.assertEqual(jwt["AllowedScopes"], ["ReadScope"])
        stmts = RES["HandlerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        self.assertEqual(stmts[0]["Action"], "dynamodb:GetItem")
        self.assertTrue(stmts[0]["Resource"].endswith(":table/${TableName}"))
        self.assertEqual(len(stmts), 2)
        self.assertNotIn("AWS::DynamoDB::Table", {res["Type"] for res in RES.values()})
