"""Gateway provisioning using the bedrock-agentcore starter toolkit."""

from __future__ import annotations

import io
import json
import time
import zipfile

import boto3
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

from .utils import get_account_id

# Tool definitions for each target — these are what Cedar policies evaluate against
TARGET_TOOLS = {
    "FileSystemTarget": [
        {
            "name": "read_file",
            "description": "Read a file from the filesystem",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to a file",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
        {
            "name": "list_directory",
            "description": "List directory contents",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ],
    "ShellTarget": [
        {
            "name": "execute",
            "description": "Execute a shell command",
            "inputSchema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    ],
    "CodeTarget": [
        {
            "name": "python_repl",
            "description": "Execute Python code in a REPL",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
        {
            "name": "execute_code",
            "description": "Execute code in a sandbox",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "language": {"type": "string"}},
                "required": ["code"],
            },
        },
    ],
    "RetrieveTarget": [
        {
            "name": "retrieve",
            "description": "Retrieve documents from a knowledge base",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"],
            },
        },
    ],
    "HttpTarget": [
        {
            "name": "http_request",
            "description": "Make an HTTP request",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "method": {"type": "string"}},
                "required": ["url", "method"],
            },
        },
    ],
    "ApiTarget": [
        {
            "name": "invoke",
            "description": "Invoke an external API endpoint",
            "inputSchema": {
                "type": "object",
                "properties": {"endpoint": {"type": "string"}, "method": {"type": "string"}},
                "required": ["endpoint"],
            },
        },
    ],
}

# Map targets to their Lambda handler source files
_TARGET_LAMBDA_MAP = {
    "FileSystemTarget": "utils/file_tool.js",
    "ShellTarget": "utils/shell_tool.js",
    "CodeTarget": "utils/restricted_tool.js",
    "RetrieveTarget": "utils/restricted_tool.js",
    "HttpTarget": "utils/restricted_tool.js",
    "ApiTarget": "utils/restricted_tool.js",
}

# Group targets by Lambda source to avoid creating duplicate functions
_LAMBDA_NAMES = {
    "utils/file_tool.js": "coding-agent-file-tool",
    "utils/shell_tool.js": "coding-agent-shell-tool",
    "utils/restricted_tool.js": "coding-agent-restricted-tool",
}


def setup_gateway(region: str) -> dict:
    """Create MCP Gateway with Cognito OAuth and tool targets.

    Returns dict with gateway_client, gateway, client_info, gateway_url, gateway_id,
    lambda_arns, iam_role_arn.
    """
    gateway_client = GatewayClient(region_name=region)

    # Create Cognito OAuth authorizer (we need client_info for tokens later)
    cognito_result = gateway_client.create_oauth_authorizer_with_cognito(
        gateway_name="coding-agent-policy-gw"
    )
    client_info = cognito_result["client_info"]
    authorizer_config = cognito_result["authorizer_config"]

    # Get or create gateway with this authorizer
    gateway = _get_or_create_gateway(gateway_client, region, authorizer_config)
    gateway_id = gateway["gatewayId"]
    gateway_url = gateway["gatewayUrl"]

    # Enable CloudWatch Logs for policy decision auditing
    _enable_log_delivery(gateway_client, gateway, region)

    # Create per-target Lambda functions
    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    role_arn = _get_or_create_lambda_role(iam_client, region)
    lambda_arns = _create_lambda_functions(lambda_client, role_arn, region)

    # Register tool targets with per-target Lambdas
    for target_name, tools in TARGET_TOOLS.items():
        source_file = _TARGET_LAMBDA_MAP[target_name]
        lambda_name = _LAMBDA_NAMES[source_file]
        lambda_arn = lambda_arns[lambda_name]
        _create_target(gateway_client, gateway, target_name, tools, lambda_arn)

    print(f"  Gateway ready: {gateway_id}")
    print(f"  URL: {gateway_url}")

    return {
        "gateway_client": gateway_client,
        "gateway": gateway,
        "client_info": client_info,
        "gateway_url": gateway_url,
        "gateway_id": gateway_id,
        "lambda_arns": lambda_arns,
        "iam_role_arn": role_arn,
    }


def _get_or_create_gateway(
    gateway_client: GatewayClient, region: str, authorizer_config: dict
) -> dict:
    """Get existing gateway or create a new one (idempotent)."""
    try:
        result = gateway_client.get_gateway(name="coding-agent-policy-gw")
        if result and "gateway" in result:
            gw = result["gateway"]
            status = gw.get("status", "")
            if status == "READY":
                print("  Reusing existing gateway: coding-agent-policy-gw")
                return gw
            # Gateway exists but is in bad state — delete and recreate
            print(f"  Gateway in {status} state, recreating...")
            try:
                gateway_client.cleanup_gateway(gateway_id=gw["gatewayId"])
            except Exception:
                pass
    except Exception:
        pass

    # Create new gateway with the provided authorizer
    gateway = gateway_client.create_mcp_gateway(
        name="coding-agent-policy-gw",
        authorizer_config=authorizer_config,
        enable_semantic_search=False,
        enable_observability=False,
    )
    return gateway


def _enable_log_delivery(gateway_client: GatewayClient, gateway: dict, region: str) -> None:
    """Enable CloudWatch Logs delivery for policy decision auditing."""
    gateway_id = gateway["gatewayId"]
    gateway_arn = gateway.get("gatewayArn")

    try:
        gateway_client.enable_observability(
            gateway_id=gateway_id,
            gateway_arn=gateway_arn,
            enable_logs=True,
            enable_traces=False,  # Skip X-Ray (often fails with ValidationException)
        )
        print("  ✓ CloudWatch Logs delivery enabled (policy decisions will be logged)")
    except Exception as e:
        err_msg = str(e)
        if "already" in err_msg.lower() or "enabled" in err_msg.lower():
            print("  ✓ CloudWatch Logs already enabled")
        else:
            # Non-blocking — observability failure shouldn't stop the demo
            print(f"  ⚠ Log delivery setup: {err_msg} (non-blocking)")


def _get_or_create_lambda_role(iam_client, region: str) -> str:
    """Create or reuse the IAM role for Lambda functions."""
    role_name = "coding-agent-policy-lambda-role"
    trust_policy = json.dumps(
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
    )

    try:
        resp = iam_client.get_role(RoleName=role_name)
        print(f"  Reusing IAM role: {role_name}")
        return resp["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating IAM role: {role_name}")
    resp = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=trust_policy,
        Description="Execution role for coding-agent-policy Lambda tools",
    )
    role_arn = resp["Role"]["Arn"]

    # Attach basic execution policy
    iam_client.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    # Wait for role propagation
    print("  Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return role_arn


def _create_lambda_functions(lambda_client, role_arn: str, region: str) -> dict[str, str]:
    """Create Lambda functions from utils/ JS files. Returns {name: arn}."""
    from pathlib import Path

    arns = {}
    for source_file, func_name in _LAMBDA_NAMES.items():
        # Check if function already exists
        try:
            resp = lambda_client.get_function(FunctionName=func_name)
            arns[func_name] = resp["Configuration"]["FunctionArn"]
            print(f"  Reusing Lambda: {func_name}")
            continue
        except lambda_client.exceptions.ResourceNotFoundException:
            pass

        # Read source and create zip in memory
        source_path = Path(source_file)
        source_code = source_path.read_text()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.js", source_code)
        zip_bytes = zip_buffer.getvalue()

        print(f"  Creating Lambda: {func_name}")
        for attempt in range(5):
            try:
                resp = lambda_client.create_function(
                    FunctionName=func_name,
                    Runtime="nodejs20.x",
                    Role=role_arn,
                    Handler="index.handler",
                    Code={"ZipFile": zip_bytes},
                    Description=f"Tool handler for coding-agent-policy sample ({source_file})",
                    Timeout=30,
                    MemorySize=128,
                )
                arns[func_name] = resp["FunctionArn"]

                # Wait for function to become active
                waiter = lambda_client.get_waiter("function_active_v2")
                waiter.wait(FunctionName=func_name)
                break
            except lambda_client.exceptions.InvalidParameterValueException as e:
                if "role" in str(e).lower() and attempt < 4:
                    print("    Role not ready, retrying in 5s...")
                    time.sleep(5)
                    continue
                raise
            except Exception as e:
                if "ResourceConflictException" in type(e).__name__:
                    resp = lambda_client.get_function(FunctionName=func_name)
                    arns[func_name] = resp["Configuration"]["FunctionArn"]
                    break
                raise

        # Add resource-based policy so Gateway can invoke
        account_id = get_account_id(region)
        try:
            lambda_client.add_permission(
                FunctionName=func_name,
                StatementId="AllowBedrockAgentCoreInvoke",
                Action="lambda:InvokeFunction",
                Principal="bedrock-agentcore.amazonaws.com",
                SourceAccount=account_id,
            )
        except lambda_client.exceptions.ResourceConflictException:
            pass  # Permission already exists

    return arns


def _create_target(
    gateway_client: GatewayClient,
    gateway: dict,
    name: str,
    tools: list[dict],
    lambda_arn: str,
) -> None:
    """Create a gateway target with custom tool schema."""
    target_payload = {
        "lambdaArn": lambda_arn,
        "toolSchema": {"inlinePayload": tools},
    }

    for attempt in range(3):
        try:
            gateway_client.create_mcp_gateway_target(
                gateway=gateway,
                name=name,
                target_type="lambda",
                target_payload=target_payload,
            )
            return
        except Exception as e:
            err_msg = str(e)
            if "ConflictException" in type(e).__name__ or "already exists" in err_msg:
                return
            if ("not ready" in err_msg or "resource conflict" in err_msg) and attempt < 2:
                print(f"    {name}: Lambda not ready, retrying in 10s...")
                time.sleep(10)
                continue
            raise
