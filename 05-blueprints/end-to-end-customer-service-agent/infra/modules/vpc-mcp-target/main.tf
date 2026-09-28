# ---------------------------------------------------------------------------
# VPC MCP Target Module — AgentCore Runtime with Entra ID OBO
# ---------------------------------------------------------------------------
# Deploys an MCP server on AgentCore Runtime with Microsoft Entra ID
# On-Behalf-Of (OBO) token exchange via the AgentCore Gateway.
#
# Architecture:
#   User (Entra JWT) → Gateway (CUSTOM_JWT) → OBO Exchange → Runtime (MCP)
#
# NOTE: The gateway target and credential provider are created via API/scripts
# because the Terraform provider does not yet support:
#   - TOKEN_EXCHANGE grant type
#   - listingMode: DYNAMIC
#   - onBehalfOfTokenExchangeConfig on credential providers
#
# After terraform apply, run:
#   python scripts/create_credential_provider.py --client-secret <secret>
#   python scripts/create_gateway_target.py \
#     --gateway-id <id> --runtime-id <id> --credential-provider-arn <arn>
# ---------------------------------------------------------------------------

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Gateway IAM Role
# ---------------------------------------------------------------------------
resource "aws_iam_role" "gateway_role" {
  name = "${var.gateway_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:gateway/*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "gateway_policy" {
  name = "${var.gateway_name}-policy"
  role = aws_iam_role.gateway_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeRuntime"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeRuntime",
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:InvokeGateway",
        ]
        Resource = "*"
      },
      {
        Sid    = "WorkloadIdentity"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
          "bedrock-agentcore:CreateWorkloadIdentity",
        ]
        Resource = [
          "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workload-identity-directory/default",
          "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workload-identity-directory/default/workload-identity/*",
        ]
      },
      {
        Sid    = "GetResourceOauth2Token"
        Effect = "Allow"
        Action = ["bedrock-agentcore:GetResourceOauth2Token"]
        Resource = [
          "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workload-identity-directory/default",
          "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workload-identity-directory/default/workload-identity/*",
          "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:token-vault/default",
          "arn:aws:bedrock-agentcore:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:token-vault/default/oauth2credentialprovider/*",
        ]
      },
      {
        Sid      = "SecretsManager"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:bedrock-agentcore*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*"
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# AgentCore Gateway with Entra ID CUSTOM_JWT inbound auth
# ---------------------------------------------------------------------------
resource "aws_bedrockagentcore_gateway" "mcp_gateway" {
  name     = var.gateway_name
  role_arn = aws_iam_role.gateway_role.arn

  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0/.well-known/openid-configuration"
      allowed_audience = [var.entra_mcp_client_id, var.entra_agent_client_id]
    }
  }

  protocol_type   = "MCP"
  exception_level = "DEBUG"
}

# ---------------------------------------------------------------------------
# MCP Server deployed on AgentCore Runtime
# ---------------------------------------------------------------------------
# The MCP server is deployed via bedrock-agentcore-starter-toolkit (see
# mcp-server/deploy_obo.py). The Runtime resource below is for reference
# and documentation — the actual deployment uses the starter toolkit CLI.
#
# Runtime configuration:
#   - Protocol: MCP
#   - Authorizer: CUSTOM_JWT (Entra ID v2.0, audience = MCP Server app)
#   - requestHeaderAllowlist: ["Authorization"] (forwards OBO token to container)
#   - Network: PUBLIC (traffic stays on AWS backbone for Runtime targets)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Gateway Target (created via API — see scripts/create_gateway_target.py)
# ---------------------------------------------------------------------------
# The Terraform provider does not support TOKEN_EXCHANGE grant type or
# DYNAMIC listing mode. After terraform apply, create the target with:
#
#   python scripts/create_gateway_target.py \
#     --gateway-id <output.gateway_id> \
#     --runtime-id <runtime-id-from-deploy> \
#     --credential-provider-arn <output from create_credential_provider.py>
# ---------------------------------------------------------------------------
