# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore Runtime
#
# Hosts the multi-agent KYC orchestrator as an ARM64 container. The image is
# built and pushed during apply; a content hash over the agent sources forces a
# rebuild and runtime replacement whenever the code changes.
# =============================================================================

# -----------------------------------------------------------------------------
# Container image
# -----------------------------------------------------------------------------

module "agent_image" {
  source = "./modules/ecr-image"

  repository_name = "${var.stack_name}-kyc-agent"
  build_context   = abspath("${local.repo_root}/backend/agent")
  region          = local.region
  account_id      = local.account_id
  container_cli   = var.container_cli

  # Any change to these rebuilds the image and, via replace_triggered_by below,
  # replaces the runtime so it picks the new image up.
  source_files = concat(
    [
      "${local.repo_root}/backend/agent/Dockerfile",
      "${local.repo_root}/backend/agent/requirements.txt",
    ],
    [for f in fileset("${local.repo_root}/backend/agent", "**/*.py") :
    "${local.repo_root}/backend/agent/${f}"],
  )
}


# -----------------------------------------------------------------------------
# Runtime IAM role
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "runtime_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.stack_name}-runtime-role"
  assume_role_policy = data.aws_iam_policy_document.runtime_assume_role.json
  description        = "Execution role for the KYC AgentCore Runtime"
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid    = "ECRImageAccess"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [module.agent_image.repository_arn]
  }

  statement {
    sid       = "ECRTokenAccess"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"]
  }

  # Unified trace + prompt storage (default for agents from 2026-08-31; opted in
  # early here via UNIFIED_TRACES_DESTINATION_ENABLED below). The agent role must
  # be able to put a resource policy on the log group so the OTEL exporter can
  # write spans alongside logs. Scoped to this account's log groups.
  # logs:PutResourcePolicy acts on the account-level CloudWatch Logs resource
  # policy, not a single log group, so IAM requires "*" here — a log-group ARN
  # would be a silent denial. The gateway into this account is still the
  # bedrock-agentcore service assuming this role, not a public principal.
  statement {
    sid       = "UnifiedTracesResourcePolicy"
    effect    = "Allow"
    actions   = ["logs:PutResourcePolicy"]
    resources = ["*"]
  }

  statement {
    sid       = "CloudWatchLogsDescribe"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:*"]
  }

  statement {
    sid    = "Observability"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "CloudWatchMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["bedrock-agentcore"]
    }
  }

  statement {
    sid    = "WorkloadIdentity"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
      "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    ]
    resources = [
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*",
    ]
  }

  statement {
    sid    = "BedrockModelInvocation"
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

  # The agents reach their tools by calling the Gateway over MCP with SigV4.
  statement {
    sid       = "InvokeGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.kyc.gateway_arn]
  }

  statement {
    sid    = "MemoryAccess"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:GetEvent",
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:ListSessions",
      "bedrock-agentcore:RetrieveMemoryRecords",
      "bedrock-agentcore:ListMemoryRecords",
    ]
    resources = [
      aws_bedrockagentcore_memory.kyc.arn,
      "${aws_bedrockagentcore_memory.kyc.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "${var.stack_name}-runtime-policy"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

# -----------------------------------------------------------------------------
# Runtime
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "kyc" {
  agent_runtime_name = local.runtime_name
  role_arn           = aws_iam_role.runtime.arn
  description        = "Multi-agent KYC onboarding risk assessment (Credit Analyst + Compliance Officer)"

  agent_runtime_artifact {
    container_configuration {
      container_uri = module.agent_image.image_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  # The demo console calls the Runtime with SigV4 through the console API, so the
  # Runtime needs no JWT authorizer of its own.
  #
  # INFERENCE_ROUTE selects between BedrockModel (direct) and Strands' OpenAI
  # provider pointed at the Gateway's /inference endpoint. GUARDRAIL_ID /
  # GUARDRAIL_VERSION are echoed to the console so the model-plane scope is
  # observable — the agent code does not read them. Binding the guardrail to
  # gateway traffic is done through AgentCore Policy (see policy.tf), which is
  # gated off until that Cedar extension ships; so the guardrail is deployed and
  # versioned but not yet enforced on the /inference path.
  environment_variables = {
    AWS_REGION  = local.region
    MEMORY_ID   = aws_bedrockagentcore_memory.kyc.id
    GATEWAY_URL = aws_bedrockagentcore_gateway.kyc.gateway_url
    MODEL_ID    = var.model_id
    STACK_NAME  = var.stack_name
    LOG_LEVEL   = "INFO"

    # Unified OTEL trace + prompt storage: traces, prompts, and logs land in one
    # resource-specific log group instead of being split across groups, so they
    # correlate on trace_id/span_id in CloudWatch Transaction Search. Becomes
    # the default for all agents on 2026-08-31; opted in early. Requires ADOT
    # >= 0.17.1 (see the agent Dockerfile) and logs:PutResourcePolicy on the
    # runtime role (see iam below).
    UNIFIED_TRACES_DESTINATION_ENABLED = "true"

    INFERENCE_ROUTE   = var.inference_route
    GATEWAY_MODEL_ID  = var.gateway_model_id
    GUARDRAIL_ID      = aws_bedrock_guardrail.kyc.guardrail_id
    GUARDRAIL_VERSION = aws_bedrock_guardrail_version.kyc.version

    # Policy plane, echoed into each assessment so the console can report that
    # enforcement happened. The agent does not act on these — the Gateway
    # authorizes server-side whether or not the agent knows about it.
    POLICY_ENGINE_ID = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
    POLICY_MODE      = var.policy_engine_mode
  }

  # No replace_triggered_by: the image is content-addressed (modules/ecr-image
  # tags by build-context hash), so container_uri changes on a code edit and the
  # runtime updates in place. Replacing it would regenerate the runtime ARN,
  # which the console API references.
  depends_on = [
    aws_iam_role_policy.runtime,
    module.agent_image,
    aws_bedrockagentcore_gateway_target.kyc_tools,
    null_resource.gateway_inference_target,
    aws_bedrockagentcore_memory_strategy.assessment_summary,
  ]
}
