import json
import logging
import os
import urllib3
import base64

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    logger.info("Interceptor received event: %s", json.dumps(event, default=str))

    mcp_data = event.get("mcp", {})
    gateway_request = mcp_data.get("gatewayRequest", {})
    headers = gateway_request.get("headers", {})
    body = gateway_request.get("body", {})

    auth_header = headers.get("authorization", "") or headers.get("Authorization", "")

    # Exchange the inbound token for a downstream-scoped token.
    # The inbound token was issued to the gateway client; the exchanged token
    # is issued to the downstream client and is the only one accepted by the
    # API Gateway Cognito authorizer.
    downstream_token = ""
    if auth_header:
        try:
            client_id = os.environ.get("DOWNSTREAM_CLIENT_ID")
            client_secret = os.environ.get("DOWNSTREAM_CLIENT_SECRET")
            cognito_domain = os.environ.get("COGNITO_DOMAIN")
            resource_server_id = os.environ.get("RESOURCE_SERVER_ID")

            if not all([client_id, client_secret, cognito_domain, resource_server_id]):
                logger.error("Missing required environment variables")
                return _error_response("Interceptor misconfigured")

            http = urllib3.PoolManager()
            token_url = f"https://{cognito_domain}/oauth2/token"

            auth_string = f"{client_id}:{client_secret}"
            auth_b64 = base64.b64encode(auth_string.encode("ascii")).decode("ascii")

            req_headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            cognito_body = f"grant_type=client_credentials&scope={resource_server_id}/read {resource_server_id}/write"

            response = http.request("POST", token_url, headers=req_headers, body=cognito_body)

            if response.status == 200:
                token_data = json.loads(response.data.decode("utf-8"))
                if "access_token" in token_data:
                    downstream_token = f"Bearer {token_data['access_token']}"
                    logger.info(downstream_token)
                    logger.info("Exchanged inbound token for downstream token")
            else:
                logger.error("Token exchange failed with status %s", response.status)
        except Exception as e:
            logger.error("Token exchange error: %s", str(e))

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": downstream_token,
                },
                "body": body,
            }
        },
    }


def _error_response(msg):
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": {"Content-Type": "application/json"},
                "body": {"error": msg},
            }
        },
    }
