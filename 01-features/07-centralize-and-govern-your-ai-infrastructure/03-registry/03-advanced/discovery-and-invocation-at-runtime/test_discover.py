import unittest
from datetime import UTC, datetime

import boto3
from botocore.stub import Stubber
from discover import discover

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

    def test_wrong_registry(self):
        self.stub.add_response(
            "search_discoverable_registry_records",
            {"registryRecords": [dict(ITEM, registryArn=REG.replace("111111111111", "222222222222"))]},
        )
        with self.assertRaises(ValueError):
            discover(self.client, REG, "orders")
