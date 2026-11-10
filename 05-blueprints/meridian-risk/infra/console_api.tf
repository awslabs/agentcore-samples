# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Console API — streaming Lambda behind a Function URL
#
# Why a Function URL rather than API Gateway: a full assessment takes 25-60s and
# streams progress the whole time. API Gateway's integration timeout is a hard
# 30s ceiling, which would sever the stream mid-assessment. Function URLs have
# no such limit and support RESPONSE_STREAM.
#
# Why a container image: Lambda's native response streaming is Node.js-only. A
# Python app streams via the Lambda Web Adapter, which is distributed as a
# container layer.
#
# Authorization is enforced twice over. The Function URL uses AWS_IAM, so the
# caller must SigV4-sign with credentials federated from the Cognito identity
# pool; the app then validates the Cognito ID token against the pool's JWKS on
# every /api route (backend/api/auth.py) to establish which operator is calling.
# =============================================================================

# -----------------------------------------------------------------------------
# Container image
# -----------------------------------------------------------------------------

module "console_api_image" {
  source = "./modules/ecr-image"

  repository_name = "${var.stack_name}-console-api"
  build_context   = abspath("${local.repo_root}/backend/api")
  region          = local.region
  account_id      = local.account_id
  container_cli   = var.container_cli

  # Globbed rather than listed file-by-file: an explicit list silently stops
  # triggering rebuilds the moment a new module is added.
  source_files = concat(
    [
      "${local.repo_root}/backend/api/Dockerfile",
      "${local.repo_root}/backend/api/requirements.txt",
    ],
    [for f in fileset("${local.repo_root}/backend/api", "*.py") :
    "${local.repo_root}/backend/api/${f}"],
  )
}

# -----------------------------------------------------------------------------
# Execution role
# -----------------------------------------------------------------------------

resource "aws_iam_role" "console_api" {
  name               = "${var.stack_name}-console-api-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  description        = "Execution role for the demo console API"
}

resource "aws_cloudwatch_log_group" "console_api" {
  name              = "/aws/lambda/${var.stack_name}-console-api"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "console_api" {
  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.console_api.arn}:*"]
  }

  # Invoke the KYC agent and read Memory.
  statement {
    sid    = "AgentCoreDataPlane"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:InvokeAgentRuntime",
      "bedrock-agentcore:ListSessions",
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:GetEvent",
      "bedrock-agentcore:RetrieveMemoryRecords",
      "bedrock-agentcore:ListMemoryRecords",
    ]
    resources = [
      aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_arn,
      "${aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_arn}/*",
      aws_bedrockagentcore_memory.kyc.arn,
      "${aws_bedrockagentcore_memory.kyc.arn}/*",
    ]
  }

  # Registry search is a data-plane call and is not resource-scopeable in the
  # preview; the control-plane statement below is scoped to this registry.
  statement {
    sid       = "RegistrySearch"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:SearchRegistryRecords"]
    resources = ["*"]
  }

  # Browse the catalog and drive the approval workflow from the console.
  statement {
    sid    = "RegistryControlPlane"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:ListRegistryRecords",
      "bedrock-agentcore:GetRegistryRecord",
      "bedrock-agentcore:SubmitRegistryRecordForApproval",
      "bedrock-agentcore:UpdateRegistryRecordStatus",
    ]
    resources = [
      aws_bedrockagentcore_registry.fsi.registry_arn,
      "${aws_bedrockagentcore_registry.fsi.registry_arn}/*",
    ]
  }

  # Inspect the Gateway's tool catalog for the Tools tab.
  statement {
    sid    = "GatewayInspection"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:ListGatewayTargets",
      "bedrock-agentcore:GetGatewayTarget",
      "bedrock-agentcore:GetGateway",
    ]
    resources = [
      aws_bedrockagentcore_gateway.kyc.gateway_arn,
      "${aws_bedrockagentcore_gateway.kyc.gateway_arn}/*",
    ]
  }

  # The tool inspector invokes the KYC tool Lambda directly.
  statement {
    sid       = "InvokeKycTools"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.kyc_tools.arn]
  }
}

resource "aws_iam_role_policy" "console_api" {
  name   = "${var.stack_name}-console-api-policy"
  role   = aws_iam_role.console_api.id
  policy = data.aws_iam_policy_document.console_api.json
}

# -----------------------------------------------------------------------------
# Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "console_api" {
  function_name = "${var.stack_name}-console-api"
  role          = aws_iam_role.console_api.arn
  package_type  = "Image"
  image_uri     = module.console_api_image.image_uri
  architectures = ["arm64"]

  # Must exceed the longest assessment; the Runtime call itself is capped at
  # 600s by the boto3 read timeout in main.py.
  timeout     = 300
  memory_size = 1024

  environment {
    variables = {
      RUNTIME_ARN         = aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_arn
      REGISTRY_ID         = aws_bedrockagentcore_registry.fsi.registry_id
      GATEWAY_ID          = aws_bedrockagentcore_gateway.kyc.gateway_id
      GATEWAY_URL         = aws_bedrockagentcore_gateway.kyc.gateway_url
      MEMORY_ID           = aws_bedrockagentcore_memory.kyc.id
      KYC_TOOLS_LAMBDA    = aws_lambda_function.kyc_tools.function_name
      USER_POOL_ID        = aws_cognito_user_pool.console.id
      USER_POOL_CLIENT_ID = aws_cognito_user_pool_client.console.id
      CONSOLE_ORIGIN      = "https://${aws_amplify_branch.console.branch_name}.${aws_amplify_app.console.id}.amplifyapp.com"

      # Model-plane facts, mirrored from the runtime's own environment so
      # /api/config can report which route and guardrail the deployment is
      # configured for. The console API does not invoke models itself; these
      # are descriptive only, which is why the same values are set in two
      # places rather than read back from the runtime at request time.
      INFERENCE_ROUTE   = var.inference_route
      GUARDRAIL_ID      = aws_bedrock_guardrail.kyc.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail_version.kyc.version
      POLICY_ENGINE_ID  = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
      POLICY_MODE       = var.policy_engine_mode

      # AgentCore Harness — the managed agent loop, when deployed. one(...)
      # yields null (→ empty env var) when enable_harness = false.
      HARNESS_ID = one(aws_bedrockagentcore_harness.kyc[*].harness_id)
    }
  }

  # No replace_triggered_by: the image is content-addressed, so image_uri
  # changes on a code edit and Terraform issues an in-place
  # update-function-code. Replacing the function instead would tear down the
  # Function URL (whose hostname is regenerated) and its resource policy — the
  # exact churn that broke the deployed frontend during the refactor.
  depends_on = [
    aws_iam_role_policy.console_api,
    aws_cloudwatch_log_group.console_api,
    module.console_api_image,
  ]
}

# Function URLs are gated by BOTH the caller's identity policy and the
# function's resource policy. Granting lambda:InvokeFunctionUrl on the role
# alone is not enough — without this the URL returns 403 before invoking the
# function, which is indistinguishable from a signing error.
resource "aws_lambda_permission" "console_api_url" {
  statement_id           = "AllowConsoleWebRoleInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.console_api.function_name
  principal              = aws_iam_role.console_web.arn
  function_url_auth_type = "AWS_IAM"
}

resource "aws_lambda_function_url" "console_api" {
  function_name = aws_lambda_function.console_api.function_name

  # This account blocks Function URLs with authorization_type NONE — unsigned
  # requests are rejected with 403 before reaching the function, even with a
  # wide-open resource policy. So callers must SigV4-sign, and the browser gets
  # signing credentials from the Cognito identity pool (infra/cognito.tf).
  #
  # Two independent checks therefore guard the API: IAM proves the caller holds
  # federated credentials, and the app validates the Cognito ID token to
  # establish *which* operator is calling.
  authorization_type = "AWS_IAM"

  # Required for SSE — BUFFERED would withhold the response until completion.
  invoke_mode = "RESPONSE_STREAM"

  cors {
    # localhost is kept so a locally-served UI can run against this deployed
    # backend (scripts/dev.sh). It grants no access on its own: CORS only tells
    # a browser which origins may *read* a response, and every request still
    # needs federated IAM credentials plus a valid ID token. With
    # allow_credentials = false there is no ambient-authority risk.
    allow_origins = [
      "https://${aws_amplify_branch.console.branch_name}.${aws_amplify_app.console.id}.amplifyapp.com",
      "http://localhost:5173",
    ]
    allow_methods = ["GET", "POST"]

    # Must list every header the browser sends, or the preflight returns 200
    # with no Access-Control-Allow-Origin and the request fails. The x-amz-*
    # entries are the SigV4 signature headers; x-id-token carries the Cognito
    # ID token, since SigV4 occupies `authorization`.
    allow_headers = [
      "authorization",
      "content-type",
      "x-id-token",
      "x-amz-date",
      "x-amz-security-token",
      "x-amz-content-sha256",
    ]

    allow_credentials = false
    max_age           = 300
  }
}
