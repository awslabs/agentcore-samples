"""
MCP Proxy Lambda - Forwards MCP requests to AgentCore Gateway.

Handles SigV4-signed proxying and rewrites 3LO elicitation URLs so that
OAuth callbacks are routed through our API Gateway callback Lambda.
"""

import json
import os
import time
import base64
import urllib.request
import urllib.parse
import urllib.error

import boto3

# SSM parameter names (static strings set by CDK to break circular deps)
GATEWAY_URL_SSM_PARAM = os.environ.get("GATEWAY_URL_SSM_PARAM", "")
CALLBACK_LAMBDA_URL_SSM_PARAM = os.environ.get("CALLBACK_LAMBDA_URL_SSM_PARAM", "")

# Direct env vars (non-cycle-causing values)
AUTH_CODE_TABLE = os.environ.get("AUTH_CODE_TABLE", "")

dynamodb_resource = boto3.resource("dynamodb")
ssm_client = boto3.client("ssm")

_ssm_cache = {}


def _get_ssm_param(param_name):
    """Fetch an SSM parameter value, caching for the lifetime of the Lambda container."""
    if param_name in _ssm_cache:
        return _ssm_cache[param_name]
    if not param_name:
        return ""
    resp = ssm_client.get_parameter(Name=param_name)
    value = resp["Parameter"]["Value"]
    _ssm_cache[param_name] = value
    return value


def _get_gateway_url():
    """Resolve GATEWAY_URL from SSM or fall back to direct env var."""
    if GATEWAY_URL_SSM_PARAM:
        return _get_ssm_param(GATEWAY_URL_SSM_PARAM)
    return os.environ.get("GATEWAY_URL", "")


def _get_callback_url():
    """Resolve CALLBACK_LAMBDA_URL from SSM or fall back to direct env var."""
    if CALLBACK_LAMBDA_URL_SSM_PARAM:
        return _get_ssm_param(CALLBACK_LAMBDA_URL_SSM_PARAM)
    return os.environ.get("CALLBACK_LAMBDA_URL", "")


def lambda_handler(event, context):
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": {"Allow": "OPTIONS, GET, POST"}}

    if path == "/mcp":
        return _proxy_to_gateway(event)
    else:
        return {"statusCode": 404}


def _proxy_to_gateway(event):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    headers = event.get("headers", {})
    body = event.get("body", "")
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body)

    req_headers = {
        "Content-Type": headers.get("content-type", "application/json"),
        "Accept": headers.get("accept", "application/json"),
    }

    for h in ["mcp-protocol-version", "mcp-session-id"]:
        if headers.get(h):
            req_headers[h.title()] = headers[h]
    req_headers["Mcp-Protocol-Version"] = "2025-11-25"

    try:
        gateway_url = _get_gateway_url()
        if not gateway_url.startswith("https://"):
            raise ValueError("Gateway URL must use the https scheme")
        if method == "POST" and body:
            data = body.encode() if isinstance(body, str) else body
            req = urllib.request.Request(gateway_url, data=data, method="POST")
        else:
            req = urllib.request.Request(gateway_url, method=method)

        for k, v in req_headers.items():
            req.add_header(k, v)

        auth = headers.get("authorization")
        if auth:
            req.add_header("Authorization", auth)

        # gateway_url scheme is validated to be https above; it is a trusted SSM value
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            resp_body = resp.read().decode()
            resp_headers = {"Content-Type": resp.headers.get("Content-Type", "application/json")}

            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                resp_headers["Mcp-Session-Id"] = session_id

            try:
                resp_data = json.loads(resp_body)
                if "error" in resp_data and resp_data["error"].get("code") == -32042:
                    resp_body = _rewrite_elicitation_urls(resp_data, auth)
            except (json.JSONDecodeError, TypeError):
                pass

            return {
                "statusCode": resp.status,
                "headers": resp_headers,
                "body": resp_body,
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        www_auth = None
        if e.code == 401:
            print("replacing resource metadata")
            www_auth = e.headers.get("WWW-Authenticate")
            if www_auth:
                parts = www_auth.split(", ")
                for p in parts:
                    if p.startswith("resource_metadata"):
                        server = p.split("=")[1]
                        elements = server.split("/")
                        elements[2] = _get_callback_url().rstrip("/")
                        server = "/".join(elements[2:])
                        res_metadata = "resource_metadata=" + server
                        parts = [p for p in parts if not p.startswith("resource_metadata")]
                        parts.append(res_metadata)
                        www_auth = ", ".join(parts)
        try:
            error_data = json.loads(error_body)
            if "error" in error_data and error_data["error"].get("code") == -32042:
                error_body = _rewrite_elicitation_urls(error_data)
        except (json.JSONDecodeError, TypeError):
            pass

        resp_headers = {"Content-Type": "application/json"}
        if www_auth:
            resp_headers["WWW-Authenticate"] = www_auth
        return {
            "statusCode": e.code,
            "headers": resp_headers,
            "body": error_body,
        }
    except Exception as e:
        print(f"proxy_to_gateway error: {e}")
        return _json_response(502, {"error": {"code": -32603, "message": "Gateway request failed"}})


def _rewrite_elicitation_urls(error_data, auth):
    print("Rewrite elicitation url")
    print(error_data)
    callback_url = _get_callback_url().rstrip("/")
    user_token = auth.split(" ")[1]
    table = dynamodb_resource.Table(AUTH_CODE_TABLE)
    elicitations = error_data.get("error", {}).get("data", {}).get("elicitations", [])
    for elicitation in elicitations:
        original_url = elicitation.get("url")
        if original_url:
            parsed = urllib.parse.urlparse(original_url)
            qs = urllib.parse.parse_qs(parsed.query)
            session_id = qs.get("request_uri", [None])[0]
            if session_id:
                table.put_item(
                    Item={
                        "code": f"elicitation:{session_id}",
                        "user_token": user_token,
                        "ttl": int(time.time()) + 300,
                    }
                )
            new_qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            new_qs["redirect_uri"] = [f"{callback_url}/oauth2/callback"]
            elicitation["url"] = urllib.parse.urlunparse(
                parsed._replace(query=urllib.parse.urlencode(new_qs, doseq=True))
            )

    return json.dumps(error_data)


def _json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
