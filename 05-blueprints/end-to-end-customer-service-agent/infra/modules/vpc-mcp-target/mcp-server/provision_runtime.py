"""
Provision the customer service MCP server on AgentCore Runtime.

Builds a container image via CodeBuild (ARM64), pushes to ECR, and creates
the AgentCore Runtime with Entra ID token validation configured. The runtime
accepts delegated tokens from the gateway and forwards the Authorization
header to the application for identity-aware tool execution.

Environment variables (optional overrides):
  ENTRA_TENANT_ID      — Azure AD directory ID
  ENTRA_MCP_CLIENT_ID  — Audience app registration client ID
  AWS_REGION           — Target region (default: us-east-1)
"""

import os
from bedrock_agentcore_starter_toolkit import Runtime

TENANT = os.environ.get("ENTRA_TENANT_ID", "<your-tenant-id>")
AUDIENCE = os.environ.get("ENTRA_MCP_CLIENT_ID", "<your-mcp-server-client-id>")
REGION = os.environ.get("AWS_REGION", "us-east-1")

print("Provisioning customer service MCP runtime")
print(f"  Region:   {REGION}")
print(f"  Tenant:   {TENANT}")
print(f"  Audience: {AUDIENCE}")
print()

runtime = Runtime()

runtime.configure(
    entrypoint="server.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="requirements.txt",
    region=REGION,
    protocol="MCP",
    agent_name="cx_mcp_server",
    authorizer_configuration={
        "customJWTAuthorizer": {
            "discoveryUrl": f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration",
            "allowedAudience": [AUDIENCE, f"api://{AUDIENCE}"],
        }
    },
    request_header_configuration={"requestHeaderAllowlist": ["Authorization"]},
)

print("Building and deploying...")
result = runtime.launch()
print()
print(f"Runtime ARN: {result.agent_arn}")
print(f"Runtime ID:  {result.agent_id}")
print()
print("Record the Runtime ID for the next step (wiring gateway target).")
