import json
import os

import boto3

DECISION_MODE_SSM_PARAM = os.environ.get("DECISION_MODE_SSM_PARAM", "/insurance-claims-demo/decision_mode")

_ssm = boto3.client("ssm")


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        mode = (body.get("mode") or "").strip().lower()

        if mode not in ("auto", "human"):
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                },
                "body": json.dumps({"error": "mode must be 'auto' or 'human'"}),
            }

        _ssm.put_parameter(Name=DECISION_MODE_SSM_PARAM, Value=mode, Type="String", Overwrite=True)

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"mode": mode}),
        }

    except Exception as e:  # noqa: BLE001 - handler boundary: return error response, never crash
        print(f"Error: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"error": "Internal server error"}),
        }
