# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore Harness — the managed agent loop, as configuration
#
# The Runtime in runtime.tf owns its orchestration loop in code: a Strands
# workflow that runs two specialists in parallel and synthesizes a verdict.
# AgentCore Harness is the *declarative* counterpart — you declare the model,
# tools, and instructions, and AgentCore runs the loop (orchestration, tool
# execution, memory, identity, observability) in an isolated microVM per
# session. AWS's own guidance: use the harness unless you need to own the loop
# (as our multi-agent supervisor pattern does).
#
# This harness is deliberately wired to the SAME governed surfaces as the
# Runtime, so the demo can show one KYC assistant expressed two ways:
#   - the same AgentCore Gateway (its five KYC tools over MCP), reached with the
#     harness's own IAM role — so AgentCore Policy authorizes the harness's tool
#     calls exactly as it does the Runtime's, and
#   - the same Bedrock model family.
#
# Provider-native (aws_bedrockagentcore_harness), no provisioner. Gated behind
# var.enable_harness so a deployment can opt out; on by default because the
# harness is a headline AgentCore capability this POC is meant to exercise.
# =============================================================================

# -----------------------------------------------------------------------------
# Harness execution role
#
# The harness assumes this role when the loop runs. Trust is the same AgentCore
# service principal the Runtime uses. Permissions follow the documented sample
# execution-role policy (model invocation, ECR-public image pull, X-Ray,
# CloudWatch logs/metrics, workload identity) plus the two this harness actually
# needs for its wiring: invoke the shared Gateway, and read/write its own managed
# memory.
# -----------------------------------------------------------------------------

locals {
  # Harness names must match ^[a-zA-Z]{0,39}$ (letters only), so strip the stack
  # name's separators. Held in a local because both the resource and the managed
  # memory IAM ARN (memory/<harness_name>-*) must use the exact same value.
  harness_name = replace(replace(var.stack_name, "-", ""), "_", "")

  # The skill's S3 key prefix — the S3 object key and the skill-URI output both
  # reference it, so keep it in one place.
  harness_skill_prefix = "skills/kyc-onboarding-assessment"

  # The skill bundle on disk. Lives under backend/ (application code the harness
  # runs on), not a top-level skills/ (which would read like developer tooling).
  harness_skill_dir = "${local.repo_root}/backend/harness/skills/kyc-onboarding-assessment"
}

# -----------------------------------------------------------------------------
# Agent skill bundle (S3)
#
# The harness's fourth capability — after model, tools, and memory — is Skills:
# AgentSkills.io bundles (a SKILL.md plus optional scripts/refs) that give the
# agent domain method on demand via progressive disclosure. This uploads the KYC
# onboarding skill and attaches it to the harness from S3.
#
# Why S3 (and why the skill is applied per-invocation): the AWS provider's
# harness resource only exposes the `path` (filesystem) skill source, not the
# `s3` / `git` / `awsSkills` sources the API supports. `path` needs the skill
# already on the microVM (custom container), which this managed-image harness
# does not have — and attaching an s3 skill to the harness resource makes the
# provider's read fail (see the note on the harness resource below). So the
# bundle lives here in S3 and is attached at invoke time via
# invoke_harness(skills=[{s3:{uri}}]); scripts/manage_harness_skill.py does that
# and is the skill smoke test. The bucket + skill object are real, deployed
# infra; only the attachment is deferred to the call.
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "harness_skills" {
  count = var.enable_harness ? 1 : 0

  # Bucket names are globally unique; the account id keeps it collision-free.
  bucket        = "${var.stack_name}-harness-skills-${local.account_id}"
  force_destroy = true # demo stack: let destroy remove the bucket with the skill in it
}

resource "aws_s3_bucket_public_access_block" "harness_skills" {
  count = var.enable_harness ? 1 : 0

  bucket                  = aws_s3_bucket.harness_skills[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload the SKILL.md. etag on the content so an edit re-uploads it; the invoke
# always reads the current object from S3, so the next call picks up the change.
resource "aws_s3_object" "harness_skill" {
  count = var.enable_harness ? 1 : 0

  bucket = aws_s3_bucket.harness_skills[0].id
  key    = "${local.harness_skill_prefix}/SKILL.md"
  source = "${local.harness_skill_dir}/SKILL.md"
  etag   = filemd5("${local.harness_skill_dir}/SKILL.md")
}

data "aws_iam_policy_document" "harness_assume_role" {
  count = var.enable_harness ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "harness" {
  count = var.enable_harness ? 1 : 0

  name               = "${var.stack_name}-harness-role"
  assume_role_policy = data.aws_iam_policy_document.harness_assume_role[0].json
  description        = "Execution role for the managed KYC AgentCore Harness"
}

data "aws_iam_policy_document" "harness" {
  count = var.enable_harness ? 1 : 0

  # Model invocation — the harness calls Bedrock for the agent loop.
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

  # The managed harness image is pulled from ECR Public on the public network.
  statement {
    sid       = "EcrPublicTokenAccess"
    effect    = "Allow"
    actions   = ["ecr-public:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "StsForEcrPublicPull"
    effect    = "Allow"
    actions   = ["sts:GetServiceBearerToken"]
    resources = ["*"]
  }

  # Observability — traces to X-Ray, logs and metrics to CloudWatch. Same shape
  # as the Runtime role so both agents land in the unified trace view.
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
  statement {
    sid       = "CloudWatchLogsDescribeGroups"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:*"]
  }
  statement {
    sid       = "CloudWatchLogsResourcePolicy"
    effect    = "Allow"
    actions   = ["logs:PutResourcePolicy"]
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

  # Workload identity for the managed loop.
  statement {
    sid    = "WorkloadIdentity"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetWorkloadAccessToken",
      "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
    ]
    resources = [
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*",
    ]
  }

  # This harness's wiring: reach the shared Gateway (its KYC tools) under the
  # same ARN the Runtime role grants.
  statement {
    sid       = "InvokeGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.kyc.gateway_arn]
  }

  # Fetch the S3 skill bundle at session start. The docs require both GetObject
  # (read the SKILL.md) and ListBucket (enumerate the skill prefix).
  statement {
    sid       = "SkillBucketRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.harness_skills[0].arn}/*"]
  }
  statement {
    sid       = "SkillBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.harness_skills[0].arn]
  }
  # The harness provisions its OWN managed short/long-term memory (the
  # managedMemoryConfiguration the service creates by default) — a resource
  # distinct from the Runtime's shared KYC memory, and named after the harness
  # (`<harness_name>-<service-assigned-id>`). Its id is not known at plan time,
  # so scope to the harness-name prefix. Without this the loop fails mid-run on
  # `ListEvents` against that managed memory.
  statement {
    sid    = "ManagedMemoryAccess"
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
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:memory/${local.harness_name}-*",
    ]
  }
}

resource "aws_iam_role_policy" "harness" {
  count = var.enable_harness ? 1 : 0

  name   = "${var.stack_name}-harness-policy"
  role   = aws_iam_role.harness[0].id
  policy = data.aws_iam_policy_document.harness[0].json
}

# -----------------------------------------------------------------------------
# The harness
#
# Model + instructions + one tool (the shared Gateway) — the whole agent as
# config. Tool calls go out under this harness's IAM role, so AgentCore Policy
# governs them exactly as it does the Runtime's. Harness names must match
# ^[a-zA-Z]{0,39}$ (letters only), so the stack name's separators are stripped.
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_harness" "kyc" {
  count = var.enable_harness ? 1 : 0

  harness_name       = local.harness_name
  execution_role_arn = aws_iam_role.harness[0].arn

  model {
    bedrock_model_config {
      model_id    = var.model_id
      temperature = 0.1
    }
  }

  system_prompt {
    text = <<-PROMPT
      You are a KYC onboarding analyst for a corporate bank. Assess a
      prospective corporate customer for credit and AML/compliance risk using
      the KYC tools available to you, and return a single onboarding decision:
      APPROVE, REJECT, or ESCALATE, with a 0-100 risk score and the factors
      behind it. Cite sanctions, PEP, adverse-media, and structuring findings
      explicitly. If you cannot complete the assessment, ESCALATE rather than
      approve. Operate only on the synthetic customers this deployment holds
      (CUST001, CUST002, CUST003).
    PROMPT
  }

  # The same governed Gateway the Runtime uses. Outbound auth is AWS IAM, so the
  # harness's execution role signs the tool calls and AgentCore Policy evaluates
  # them server-side — the demo's authorization story holds for both agents.
  tool {
    name = "kyc_gateway"
    type = "agentcore_gateway"
    config {
      agentcore_gateway {
        gateway_arn = aws_bedrockagentcore_gateway.kyc.gateway_arn
        outbound_auth {
          aws_iam = true
        }
      }
    }
  }

  # Cap the loop so a runaway assessment cannot spend unbounded tokens/time.
  max_iterations  = 15
  max_tokens      = 8192
  timeout_seconds = 300

  tags = local.common_tags
}

# NOTE — why the skill is applied at INVOKE time, not persisted on the harness.
# The service supports s3/git/awsSkills skill sources, but the AWS provider's
# harness resource models only the `path` source: attaching an s3 skill to the
# harness (via update_harness) makes the provider's next *read* fail outright
# ("reading Bedrock AgentCore Harness: Unsupported Type — skill flatten:
# HarnessSkillMemberS3"), which bricks plan/apply for the whole stack — and
# lifecycle ignore_changes cannot help, because the failure is at refresh, not
# diff. So this stack ships the skill bundle in S3 (above) and attaches it
# per-invocation instead: invoke_harness(skills=[{s3:{uri}}]) loads the SKILL.md
# for that call without mutating the harness config the provider has to read.
# scripts/manage_harness_skill.py does exactly that. Persist the skill on the
# resource once the provider learns the s3 source.
