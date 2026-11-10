# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore Gateway
#
# Exposes the five KYC data tools to the agents over MCP. Authorization is
# AWS_IAM, so the Runtime's execution role authorizes tool calls directly with
# SigV4 — no Cognito resource server, machine client, or token vault needed.
# =============================================================================

# -----------------------------------------------------------------------------
# KYC tool Lambda
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "kyc_tools" {
  name              = "/aws/lambda/${var.stack_name}-kyc-tools"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kyc_tools_lambda" {
  name               = "${var.stack_name}-kyc-tools-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  description        = "Execution role for the KYC tool Lambda"
}

data "aws_iam_policy_document" "kyc_tools_lambda" {
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.kyc_tools.arn}:*"]
  }
}

resource "aws_iam_role_policy" "kyc_tools_lambda" {
  name   = "${var.stack_name}-kyc-tools-policy"
  role   = aws_iam_role.kyc_tools_lambda.id
  policy = data.aws_iam_policy_document.kyc_tools_lambda.json
}

# The handler and its fixtures live in separate top-level directories
# (backend/gateway and data/), but the Lambda resolves fixtures relative to the
# handler, so the zip must carry data/ alongside the module. archive_file cannot
# merge two source trees, hence the explicit source blocks.
data "archive_file" "kyc_tools" {
  type        = "zip"
  output_path = "${path.module}/.artifacts/kyc_tools.zip"

  source {
    filename = "kyc_tools_lambda.py"
    content  = file("${local.repo_root}/backend/gateway/kyc_tools_lambda.py")
  }

  dynamic "source" {
    for_each = fileset("${local.repo_root}/data", "**/*.json")
    content {
      filename = "data/${source.value}"
      content  = file("${local.repo_root}/data/${source.value}")
    }
  }
}

resource "aws_lambda_function" "kyc_tools" {
  function_name = "${var.stack_name}-kyc-tools"
  role          = aws_iam_role.kyc_tools_lambda.arn
  handler       = "kyc_tools_lambda.handler"
  runtime       = "python3.13"
  architectures = ["arm64"]
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.kyc_tools.output_path
  source_code_hash = data.archive_file.kyc_tools.output_base64sha256

  depends_on = [
    aws_cloudwatch_log_group.kyc_tools,
    aws_iam_role_policy.kyc_tools_lambda,
  ]
}

# -----------------------------------------------------------------------------
# Gateway IAM role
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "gateway_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${var.stack_name}-gateway-role"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume_role.json
  description        = "Role assumed by AgentCore Gateway to invoke KYC tools"
}

data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "InvokeKycToolLambda"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.kyc_tools.arn]
  }

  # Inference-target permissions. The gateway assumes this role to call
  # Bedrock on behalf of the caller — that is what turns it into an LLM
  # gateway rather than a tool proxy. Scoped to foundation-models and
  # inference-profiles so this role cannot invoke arbitrary provisioned
  # throughput or custom-model endpoints.
  statement {
    sid    = "InvokeBedrockModelsForInferenceTarget"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:${local.region}:${local.account_id}:inference-profile/*",
    ]
  }

  # The bedrock-mantle connector is a DISTINCT service from bedrock, with its
  # own action namespace and its own `project/*` resource type. Without these
  # the target reaches ACTIVE-then-FAILED: the gateway's first act is a
  # ListModels call for model discovery, which 401s under a bedrock-only
  # policy. Mirrors the AWS-managed AmazonBedrockMantleInferenceAccess policy,
  # narrowed to this account.
  statement {
    sid    = "BedrockMantleInference"
    effect = "Allow"
    actions = [
      "bedrock-mantle:Get*",
      "bedrock-mantle:List*",
      "bedrock-mantle:CreateInference",
    ]
    resources = ["arn:aws:bedrock-mantle:${local.region}:${local.account_id}:project/*"]
  }

  # CallWithBearerToken cannot be resource-scoped — the managed policy uses
  # "*" for the same reason. It is the connector's own auth exchange, not a
  # data-plane action.
  statement {
    sid       = "BedrockMantleCallWithBearerToken"
    effect    = "Allow"
    actions   = ["bedrock-mantle:CallWithBearerToken"]
    resources = ["*"]
  }

  # Marketplace subscribe — the third statement of the AWS-managed
  # AmazonBedrockMantleInferenceAccess policy, and easy to miss. Bedrock
  # auto-subscribes a third-party model on its first invocation; without these
  # the first call to a not-yet-subscribed model (e.g. any Anthropic model this
  # account has not used through bedrock-mantle before) fails with a
  # permission error that reads like "model not available for this account".
  # The CalledVia condition scopes it to calls the connector itself makes.
  statement {
    sid    = "MarketplaceOperationsFromBedrockMantle"
    effect = "Allow"
    actions = [
      "aws-marketplace:Subscribe",
      "aws-marketplace:ViewSubscriptions",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:CalledViaLast"
      values   = ["bedrock-mantle.amazonaws.com"]
    }
  }

  # Guardrail application on every request that flows through the
  # inference target. The gateway invokes ApplyGuardrail server-side,
  # so this permission belongs on the gateway role, not the caller.
  statement {
    sid    = "ApplyGuardrailOnInferenceTraffic"
    effect = "Allow"
    actions = [
      "bedrock:ApplyGuardrail",
      "bedrock:GetGuardrail",
    ]
    resources = [
      aws_bedrock_guardrail.kyc.guardrail_arn,
      "${aws_bedrock_guardrail.kyc.guardrail_arn}:*",
    ]
  }

  # Policy Engine guardrail evaluation. The Policy data plane uses Forward
  # Access Session credentials derived from THIS role to call the Bedrock
  # Guardrails API, so the permission belongs here rather than on the caller
  # or the policy engine. Resource must be "*": InvokeGuardrailChecks is the
  # built-in checker behind `when guardrails { ... }` and is not scoped to a
  # guardrail resource we own — which is also why it cannot be narrowed to
  # aws_bedrock_guardrail.kyc.
  statement {
    sid       = "PolicyEngineGuardrailChecks"
    effect    = "Allow"
    actions   = ["bedrock:InvokeGuardrailChecks"]
    resources = ["*"]
  }

  # Policy engine attachment and evaluation. Mirrors the AWS-documented policy
  # at bedrock-agentcore/latest/devguide/policy-permissions.html.
  #
  # AuthorizeAction and PartiallyAuthorizeActions are *permission-only* actions:
  # they have no corresponding API operation, so they cannot be discovered from
  # the service model — only from the service-authorization reference. Both are
  # authorized against the policy engine AND the gateway; granting one resource
  # or one action just moves the denial to the next.
  #
  # PartiallyAuthorizeActions is what lets a caller list the tools it may call,
  # so it is required for tools/list to work under a policy engine — not only
  # for tools/call.
  statement {
    sid    = "PolicyEngineConfiguration"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetPolicyEngine",
      "bedrock-agentcore:GetPolicyEngineSummary",
    ]
    resources = [aws_bedrockagentcore_policy_engine.kyc.policy_engine_arn]
  }

  statement {
    sid    = "PolicyEngineAuthorization"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:AuthorizeAction",
      "bedrock-agentcore:PartiallyAuthorizeActions",
    ]
    # The gateway ARN is a wildcard rather than a reference, to avoid a cycle:
    # aws_bedrockagentcore_gateway.kyc consumes this policy document through
    # its policy_engine_configuration, so it cannot also be an input to it.
    # Scoped to this account and region, and this role is only assumable by
    # bedrock-agentcore.amazonaws.com.
    resources = [
      aws_bedrockagentcore_policy_engine.kyc.policy_engine_arn,
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:gateway/*",
    ]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/*"]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = "${var.stack_name}-gateway-policy"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

# Gateway target creation fails if the role policy has not propagated yet.
resource "time_sleep" "gateway_iam_propagation" {
  create_duration = "15s"
  depends_on      = [aws_iam_role_policy.gateway]
}

# -----------------------------------------------------------------------------
# Gateway
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_gateway" "kyc" {
  name        = "${var.stack_name}-gateway"
  role_arn    = aws_iam_role.gateway.arn
  description = "MCP gateway exposing KYC data tools to the assessment agents"

  protocol_type = "MCP"
  protocol_configuration {
    mcp {
      supported_versions = ["2025-03-26"]
    }
  }

  # SigV4 authorization: callers present IAM credentials, not a bearer token.
  authorizer_type = "AWS_IAM"

  # Enforceable guardrails. The engine only references the gateway by ARN from
  # inside its policies, and the gateway references the engine by ARN here —
  # Terraform resolves this because the policy resources depend on the gateway,
  # not the reverse, so there is no cycle.
  #
  # mode is a variable and defaults to LOG_ONLY: ENFORCE is default-deny, so
  # this attachment can silently break every tool call if the baseline permit
  # does not match real traffic. Observe in the logs, then flip.
  policy_engine_configuration {
    arn  = aws_bedrockagentcore_policy_engine.kyc.policy_engine_arn
    mode = var.policy_engine_mode
  }

  depends_on = [time_sleep.gateway_iam_propagation]
}

# -----------------------------------------------------------------------------
# Gateway target — the KYC tool Lambda
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Gateway target — Bedrock Mantle inference connector
#
# The provider does not (yet) support inference targets under
# target_configuration, so we shell to the AWS SDK the same way we do for
# Registry records. This turns the gateway into an LLM gateway: the same
# endpoint that fronts tools now also fronts model invocation, applying
# the shared guardrail on every LLM call regardless of upstream provider.
# -----------------------------------------------------------------------------

resource "null_resource" "gateway_inference_target" {
  triggers = {
    gateway_id        = aws_bedrockagentcore_gateway.kyc.gateway_id
    guardrail_arn     = aws_bedrock_guardrail.kyc.guardrail_arn
    guardrail_version = aws_bedrock_guardrail_version.kyc.version
    script_hash       = filesha256("${local.repo_root}/scripts/manage_inference_target.py")

    # The connector performs model discovery at creation time, so a target
    # created before the gateway role can call bedrock-mantle:ListModels
    # lands in FAILED and never recovers. Keying on the policy document
    # forces a fresh target whenever those permissions change.
    gateway_policy = sha256(data.aws_iam_policy_document.gateway.json)

    # Captured for the destroy provisioner, which may only read self.triggers.
    region = local.region
    python = local.python
    script = "${local.repo_root}/scripts/manage_inference_target.py"
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/manage_inference_target.py" \
        --region "${local.region}" \
        --gateway-id "${aws_bedrockagentcore_gateway.kyc.gateway_id}" \
        --guardrail-arn "${aws_bedrock_guardrail.kyc.guardrail_arn}" \
        --guardrail-version "${aws_bedrock_guardrail_version.kyc.version}"
    EOT
  }

  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command = join(" ", [
      self.triggers.python, self.triggers.script,
      "--region", self.triggers.region,
      "--gateway-id", self.triggers.gateway_id,
      "--delete",
    ])
  }

  depends_on = [
    aws_bedrockagentcore_gateway.kyc,
    aws_iam_role_policy.gateway,
    aws_bedrock_guardrail_version.kyc,
  ]
}

resource "aws_bedrockagentcore_gateway_target" "kyc_tools" {
  name               = "kyc-tools"
  gateway_identifier = aws_bedrockagentcore_gateway.kyc.gateway_id
  description        = "Five KYC data-retrieval tools backed by one Lambda"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  # NOTE — provider quirk with a policy engine attached. The service adds
  # x-amzn-bedrock-agentcore-policy-session-id to allowed_request_headers so the
  # policy session id reaches the target. This collides with the AWS provider in
  # a way that has no clean config-only fix today:
  #   - Omitting the block, or declaring it empty, makes the create read-back
  #     inconsistent ("allowed_request_headers was null, but now [the header]").
  #   - Declaring the header is rejected: the provider forbids setting any
  #     X-Amzn-* header (it is service-reserved).
  #   - lifecycle ignore_changes does not apply — this is an apply-time
  #     consistency check, not a subsequent-plan diff.
  # The target IS created despite the error, so the accepted workaround is to let
  # the first apply surface the (non-fatal) inconsistency, then re-run: on the
  # second apply the state already carries the header and the plan is clean.
  # docs/deployment.md documents this in Troubleshooting. Tracked upstream as a
  # provider bug.
  metadata_configuration {}

  # ignore_changes covers the *steady state*: once created, the state carries
  # the service-injected header and Terraform must not try to reconcile it
  # (that would tainted-replace the target on every plan). It does not help on
  # the very first create — see the note above for the run-apply-twice caveat.
  lifecycle {
    ignore_changes = [metadata_configuration]
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.kyc_tools.arn

        # Tool schemas are generated from gateway/tools/kyc_tools/tool_spec.json
        # so the Lambda and the Gateway cannot drift apart.
        tool_schema {
          dynamic "inline_payload" {
            for_each = local.kyc_tool_specs
            content {
              name        = inline_payload.value.name
              description = inline_payload.value.description

              input_schema {
                type        = inline_payload.value.inputSchema.type
                description = inline_payload.value.inputSchema.description

                dynamic "property" {
                  for_each = inline_payload.value.inputSchema.properties
                  content {
                    name        = property.key
                    type        = property.value.type
                    description = property.value.description
                    required = contains(
                      inline_payload.value.inputSchema.required, property.key
                    )
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [aws_bedrockagentcore_gateway.kyc]
}
