import base64
import logging

import boto3

logger = logging.getLogger(__name__)


def get_ecr_token(region: str = "us-east-1") -> str:
    client = boto3.client("ecr", region_name=region)
    response = client.get_authorization_token()
    auth_data = response["authorizationData"][0]
    token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
    # Token format is "AWS:<password>"
    return token.split(":", 1)[1]
