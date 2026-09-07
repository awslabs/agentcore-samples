import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DECISION_MODE_SSM_PARAM = os.environ.get("DECISION_MODE_SSM_PARAM", "/insurance-claims-demo/decision_mode")
DEFAULT_MODE = "auto"

_ssm = boto3.client("ssm")


def handler(event, context):
    try:
        value = _ssm.get_parameter(Name=DECISION_MODE_SSM_PARAM)["Parameter"]["Value"]
        mode = value.strip().lower() if value else DEFAULT_MODE
        if mode not in ("auto", "human"):
            mode = DEFAULT_MODE
    except (ClientError, BotoCoreError):
        mode = DEFAULT_MODE

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
        },
        "body": json.dumps({"mode": mode}),
    }
