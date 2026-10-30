import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

MEMORY_ID_SSM_PARAM = os.environ.get("MEMORY_ID_SSM_PARAM", "/insurance-claims-demo/memory_id")

_ssm = boto3.client("ssm")
_bac = boto3.client("bedrock-agentcore")


def _get_ssm(param, default=""):
    try:
        return _ssm.get_parameter(Name=param)["Parameter"]["Value"]
    except (ClientError, BotoCoreError):
        return default


MEMORY_ID = _get_ssm(MEMORY_ID_SSM_PARAM)


def _parse_record(r):
    text = (r.get("content") or {}).get("text", "")
    score = r.get("score")
    parsed = None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        pass
    meta = r.get("metadata") or {}
    flat_meta = {}
    for k, v in meta.items():
        if isinstance(v, dict):
            flat_meta[k] = v.get("stringValue") or v.get("numberValue") or str(v.get("dateTimeValue", ""))
        else:
            flat_meta[k] = v
    return {
        "recordId": r.get("memoryRecordId"),
        "score": score,
        "namespaces": r.get("namespaces"),
        "text": text,
        "parsed": parsed,
        "metadata": flat_meta,
    }


def handler(event, context):
    try:
        params = event.get("queryStringParameters") or {}
        query = params.get("query", "")
        namespace = params.get("namespace", "claims/")
        grounding = params.get("grounding", "")

        try:
            top_k = int(params.get("topK", "5"))
        except (ValueError, TypeError):
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                },
                "body": json.dumps({"error": "topK must be an integer"}),
            }
        top_k = max(1, min(top_k, 10))

        if not query:
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                },
                "body": json.dumps({"error": "query parameter is required"}),
            }

        memory_id = MEMORY_ID or _get_ssm(MEMORY_ID_SSM_PARAM)

        kwargs = {
            "memoryId": memory_id,
            "namespace": namespace,
            "searchCriteria": {
                "searchQuery": query,
                "topK": top_k,
            },
        }

        if grounding in ("human_adjuster", "agent_only"):
            kwargs["searchCriteria"]["metadataFilters"] = [
                {
                    "left": {"metadataKey": "grounding_source"},
                    "operator": "EQUALS_TO",
                    "right": {"metadataValue": {"stringValue": grounding}},
                }
            ]

        resp = _bac.retrieve_memory_records(**kwargs)
        records = resp.get("memoryRecordSummaries", [])

        results = [_parse_record(r) for r in records]

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps(
                {
                    "query": query,
                    "namespace": namespace,
                    "grounding": grounding or "all",
                    "count": len(results),
                    "results": results,
                },
                default=str,
            ),
        }

    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"error": "Internal server error"}),
        }
