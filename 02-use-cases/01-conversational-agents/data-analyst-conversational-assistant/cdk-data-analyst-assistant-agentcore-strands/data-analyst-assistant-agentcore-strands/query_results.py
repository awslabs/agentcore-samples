"""Persist only post-Gateway results with server-bound ownership, never model IDs."""

import json
import os
import time
from uuid import UUID

import boto3
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry


class QueryResults(HookProvider):
    def __init__(self, actor, prompt, client=None):
        self.key = f"{actor}#{UUID(prompt)}"
        self.client = client
        self.table = os.environ["QUESTION_ANSWERS_TABLE"]

    def db(self):
        if self.client is None:
            self.client = boto3.client("dynamodb")
        return self.client

    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(AfterToolCallEvent, self.save)

    def save(self, event: AfterToolCallEvent):
        if (
            event.tool_use["name"] != "DatabaseToolsTarget___execute_sql_query"
            or event.result.get("status") != "success"
        ):
            return
        for content in event.result.get("content", []):
            if "text" not in content:
                continue
            try:
                data = json.loads(content["text"])
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("result"), list):
                continue
            # Well below DynamoDB's 400KiB item limit, including UTF-8 and metadata.
            encoded = json.dumps(data)
            if len(encoded.encode()) > 280000:
                raise ValueError("Gateway query result exceeds persistence limit")
            args = event.tool_use.get("input", {})
            self.db().put_item(
                TableName=self.table,
                Item={
                    "id": {"S": self.key},
                    "my_timestamp": {"N": str(time.time_ns())},
                    "sql_query": {"S": str(args.get("sql_query", ""))[:16000]},
                    "sql_query_description": {"S": str(args.get("description", ""))[:2000]},
                    "data": {"S": encoded},
                },
            )

    def read(self):
        response = self.db().query(
            TableName=self.table,
            KeyConditionExpression="id = :id",
            ExpressionAttributeValues={":id": {"S": self.key}},
            ConsistentRead=True,
        )
        return [
            {
                "query": item["sql_query"]["S"],
                "query_description": item["sql_query_description"]["S"],
                "query_results": json.loads(item["data"]["S"])["result"],
            }
            for item in response.get("Items", [])
        ]


def memory_facts(actor):
    response = boto3.client("bedrock-agentcore").list_memory_records(
        memoryId=os.environ["MEMORY_ID"], namespace=f"/facts/{actor}", maxResults=100
    )
    return [
        {
            "memoryRecordId": record["memoryRecordId"],
            "content": record.get("content", {}).get("text", ""),
            "createdAt": str(record.get("createdAt", "")),
            "namespace": f"/facts/{actor}",
        }
        for record in response.get("memoryRecordSummaries", [])
    ]
