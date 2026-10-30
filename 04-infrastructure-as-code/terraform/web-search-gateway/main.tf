data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # The Web Search connector is an AWS-owned resource: note the literal `aws`
  # where an account ID would normally go. Authorization is enforced per
  # invocation against this ARN.
  web_search_tool_arn = "arn:${local.partition}:bedrock-agentcore:${var.region}:aws:tool/web-search.v1"

  # Target-level domain governance. Only emit the keys that are actually set,
  # so an unfiltered PoC sends `{}` rather than empty arrays.
  #
  # NOTE the shape: at the TARGET level `domainFilter` sits at the top of
  # parameterValues. At REQUEST level (tools/call arguments) the same object is
  # nested under `filters`. The two are easy to confuse.
  domain_filter = merge(
    length(var.domain_include) > 0 ? { include = var.domain_include } : {},
    length(var.domain_exclude) > 0 ? { exclude = var.domain_exclude } : {},
  )

  parameter_values = length(local.domain_filter) > 0 ? jsonencode({ domainFilter = local.domain_filter }) : jsonencode({})

  # What the calling agent sees over MCP: target name + "___" + tool name.
  tool_name = "${var.target_name}___WebSearch"
}

# ---------------------------------------------------------------------------
# Gateway service role (outbound auth)
#
# This is the whole outbound-credential story: the Gateway authenticates to the
# AWS-managed Web Search backend with this role, so there are no search API
# keys or secrets to store anywhere.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "gateway_assume" {
  statement {
    sid     = "AllowAgentCoreAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    # Scoped to account + Region + resource type. We deliberately do NOT narrow
    # this to the exact Gateway ARN: the Gateway depends on this role via
    # role_arn, so referencing the Gateway ARN here would make Terraform report
    # a dependency cycle. The *permissions* policy below is a separate resource
    # and so can reference the exact ARN.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:bedrock-agentcore:${var.region}:${local.account_id}:gateway/*"]
    }
  }
}

resource "aws_iam_role" "gateway_service" {
  name               = "${var.name_prefix}-gateway-role"
  description        = "Outbound auth for the AgentCore Web Search Gateway"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume.json
}

data "aws_iam_policy_document" "gateway_service" {
  statement {
    sid       = "InvokeGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.web_search.gateway_arn]
  }

  statement {
    sid       = "InvokeWebSearch"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeWebSearch"]
    resources = [local.web_search_tool_arn]
  }

  # Deliberately absent: bedrock:InvokeModel. Model access belongs to whatever
  # identity runs the agent, not to the Gateway service role.
}

resource "aws_iam_role_policy" "gateway_service" {
  name   = "${var.name_prefix}-gateway-policy"
  role   = aws_iam_role.gateway_service.id
  policy = data.aws_iam_policy_document.gateway_service.json
}

# ---------------------------------------------------------------------------
# The Gateway — an MCP-compatible endpoint with IAM (SigV4) inbound auth
# ---------------------------------------------------------------------------

resource "aws_bedrockagentcore_gateway" "web_search" {
  name        = "${var.name_prefix}-gateway"
  description = "Managed web search for agents (AgentCore Web Search connector)"
  role_arn    = aws_iam_role.gateway_service.arn

  # IAM inbound auth: callers SigV4-sign their requests and the Gateway
  # evaluates the caller's IAM permissions. No authorizer_configuration block is
  # needed (that is only for CUSTOM_JWT).
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"

  protocol_configuration {
    mcp {
      supported_versions = var.mcp_supported_versions

      # search_type = "SEMANTIC" is intentionally omitted. It can only be set at
      # creation, and for a single-tool Gateway it adds per-call cost with no
      # discovery benefit.
    }
  }

  # kms_key_arn is intentionally omitted — the Gateway is encrypted with an
  # AWS-owned key. Supplying a customer-managed key would require a key policy
  # granting this role kms:Decrypt/GenerateDataKey/CreateGrant conditioned on
  # the Gateway ARN in the encryption context, which cannot be expressed in a
  # single apply. See README "Deliberate simplifications".
}

# ---------------------------------------------------------------------------
# The Web Search target — the AWS-managed connector
# ---------------------------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "web_search" {
  name               = var.target_name
  description        = "AWS-managed Web Search connector"
  gateway_identifier = aws_bedrockagentcore_gateway.web_search.gateway_id

  # The Gateway uses its own service role to reach the Web Search backend.
  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      connector {
        source {
          connector_id = "web-search"
          version      = var.connector_version
        }

        configuration {
          name             = "WebSearch"
          parameter_values = local.parameter_values
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = length(var.domain_include) == 0 || var.connector_version == "1.2.0"
      error_message = "domain_include (the target-level allowlist) requires connector_version 1.2.0. Either set connector_version = \"1.2.0\" or use domain_exclude instead."
    }
  }

  # The service role must be able to call InvokeWebSearch before the connector
  # target provisions; IAM has no implicit ordering here.
  depends_on = [aws_iam_role_policy.gateway_service]
}
