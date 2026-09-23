"""
Setup script: Deploys a minimal MCP test server on AWS Lambda + HTTP API Gateway.

No SAM or CDK required — pure boto3.

The MCP server exposes two tools:
  - get_time: returns current UTC time
  - echo: echoes a message back

Usage:
    python setup_mcp_server.py

Outputs:
    mcp_server_config.json  { "endpoint": "https://..." }
"""

import boto3
import io
import json
import time
import zipfile
from boto3.session import Session

FUNCTION_NAME = "AgentCoreMCPTestServer"
ROLE_NAME = "AgentCoreMCPLambdaRole"
API_NAME = "AgentCoreMCPTestAPI"

# Zero-dependency Lambda handler implementing the MCP Streamable HTTP protocol
_LAMBDA_CODE = """
import json
from datetime import datetime, timezone

TOOLS = [
    {
        "name": "get_time",
        "description": "Get the current UTC time",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "echo",
        "description": "Echo a message back",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Message to echo"}},
            "required": ["message"],
        },
    },
]


def handle_request(body):
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    if method == "initialize":
        result = {
            # echo back client version
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "MCPTestServer", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "get_time":
            text = datetime.now(timezone.utc).isoformat()
        elif name == "echo":
            text = f"Echo: {args.get('message', '')}"
        else:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                "id": req_id,
            }
        result = {"content": [{"type": "text", "text": text}]}
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications require no response
    else:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id,
        }

    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        if isinstance(body, list):
            responses = [r for r in [handle_request(r) for r in body] if r is not None]
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(responses),
            }
        response = handle_request(body)
        if response is None:
            return {"statusCode": 202, "body": ""}
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response),
        }
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
"""


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", _LAMBDA_CODE)
    return buf.getvalue()


def _get_or_create_role(iam) -> str:
    try:
        role = iam.get_role(RoleName=ROLE_NAME)
        print(f"  Using existing role: {ROLE_NAME}")
        return role["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating IAM role '{ROLE_NAME}'...")
    role = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        ),
    )
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print("  Waiting for role to propagate...")
    time.sleep(10)
    return role["Role"]["Arn"]


def main():
    session = Session()
    region = session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    iam = boto3.client("iam")
    lam = boto3.client("lambda", region_name=region)
    apigw = boto3.client("apigatewayv2", region_name=region)

    print("=== Deploying MCP Test Server ===")

    print("\nStep 1: IAM role...")
    role_arn = _get_or_create_role(iam)
    print(f"  Role ARN: {role_arn}")

    print("\nStep 2: Lambda function...")
    zip_bytes = _make_zip()
    try:
        lam.get_function(FunctionName=FUNCTION_NAME)
        print(f"  Updating existing function '{FUNCTION_NAME}'...")
        lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
    except lam.exceptions.ResourceNotFoundException:
        print(f"  Creating function '{FUNCTION_NAME}'...")
        lam.create_function(
            FunctionName=FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=30,
        )
        lam.get_waiter("function_active").wait(FunctionName=FUNCTION_NAME)

    fn_arn = lam.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["FunctionArn"]
    print(f"  Function ARN: {fn_arn}")

    print("\nStep 3: HTTP API Gateway...")
    existing = next(
        (a for a in apigw.get_apis().get("Items", []) if a["Name"] == API_NAME),
        None,
    )

    if existing:
        api_id = existing["ApiId"]
        print(f"  Using existing API: {api_id}")
    else:
        api = apigw.create_api(
            Name=API_NAME,
            ProtocolType="HTTP",
            CorsConfiguration={
                "AllowOrigins": ["*"],
                "AllowMethods": ["POST", "OPTIONS"],
                "AllowHeaders": ["Content-Type"],
            },
        )
        api_id = api["ApiId"]
        print(f"  Created API: {api_id}")

        lam.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId="AllowAPIGatewayInvoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*",
        )

        integration_id = apigw.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=fn_arn,
            PayloadFormatVersion="2.0",
        )["IntegrationId"]

        apigw.create_route(
            ApiId=api_id,
            RouteKey="POST /mcp",
            Target=f"integrations/{integration_id}",
        )

        apigw.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)

    endpoint = f"https://{api_id}.execute-api.{region}.amazonaws.com/mcp"
    print("\n=== MCP Server Ready ===")
    print(f"Endpoint: {endpoint}")

    config = {"endpoint": endpoint, "api_id": api_id, "function_name": FUNCTION_NAME}
    with open("mcp_server_config.json", "w") as f:
        json.dump(config, f, indent=2)
    print("Configuration saved to mcp_server_config.json")
    return config


if __name__ == "__main__":
    main()
