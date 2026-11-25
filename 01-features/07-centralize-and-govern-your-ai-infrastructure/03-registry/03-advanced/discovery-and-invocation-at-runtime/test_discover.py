import json
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import boto3
from botocore.stub import Stubber
from discover import discover, main

REG = "arn:aws:agent-registry:us-east-1:111111111111:registry/abcd12345678"
STAMP = datetime.now(UTC)
ITEM = {
    "registryArn": REG,
    "recordArn": REG + "/record/abcd12345678",
    "recordId": "abcd12345678",
    "name": "fixture",
    "recordType": "AGENT",
    "status": "APPROVED",
    "recordVersion": "1",
    "createdAt": STAMP,
    "updatedAt": STAMP,
    "descriptors": {"a2aAgentCard": {"data": '{"url":"https://untrusted.invalid"}'}},
}


class Search(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client(
            "agent-registry", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )
        self.stub = self.enterContext(Stubber(self.client))
        self.addCleanup(self.stub.assert_no_pending_responses)

    def test_search_filters_and_deduplicates(self):
        params = {"registryIds": [REG], "searchQuery": "orders", "maxResults": 10}
        self.stub.add_response(
            "search_discoverable_registry_records",
            {"registryRecords": [dict(ITEM, status="DRAFT"), ITEM], "nextToken": "next"},
            params,
        )
        self.stub.add_response(
            "search_discoverable_registry_records", {"registryRecords": [ITEM]}, {**params, "nextToken": "next"}
        )
        self.assertEqual(
            discover(self.client, REG, "orders"),
            [
                {
                    "recordArn": ITEM["recordArn"],
                    "name": "fixture",
                    "version": "1",
                    "metadataKeys": ["url"],
                    "descriptor": "a2aAgentCard",
                }
            ],
        )

    def test_cli_record_without_version(self):
        # Live approved inline records can omit the field marked required by Stubber.
        item = dict(ITEM)
        del item["recordVersion"]
        with (
            patch("sys.argv", ["discover", "--registry-arn", REG, "--query", "orders"]),
            patch("boto3.Session", return_value=Mock(client=Mock(return_value=self.client))),
            patch.object(self.client, "search_discoverable_registry_records", return_value={"registryRecords": [item]}),
            patch.object(self.client, "close", wraps=self.client.close) as close,
            patch("builtins.print") as output,
        ):
            main()
        self.assertIsNone(json.loads(output.call_args.args[0])[0]["version"])
        close.assert_called_once_with()

    def test_wrong_registry(self):
        self.stub.add_response(
            "search_discoverable_registry_records",
            {"registryRecords": [dict(ITEM, registryArn=REG.replace("111111111111", "222222222222"))]},
        )
        with self.assertRaises(ValueError):
            discover(self.client, REG, "orders")
