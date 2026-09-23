# Data sources
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# Agent Container Image
module "container_image" {
  source = "./modules/container-image"

  force_image_rebuild = var.force_image_rebuild
  image_build_tool    = var.container_image_build_tool
  repository_name     = "langgraph-cx-agent"
}

# Agent Memory
resource "aws_bedrockagentcore_memory" "agent_memory" {
  name                  = "CxMemory"
  event_expiry_duration = 30
}

# Bedrock Agent Role
module "bedrock_role" {
  source                   = "./modules/agentcore-iam-role"
  agent_memory_arn         = aws_bedrockagentcore_memory.agent_memory.arn
  container_repository_arn = module.container_image.ecr_repository_arn
  role_name                = var.bedrock_role_name
  knowledge_base_id        = module.kb_stack.knowledge_base_id
  guardrail_id             = module.guardrail.guardrail_id
  secrets_kms_key_arn      = module.secrets.kms_key_arn
  parameters_kms_key_arn   = module.parameters.kms_key_arn

  depends_on = [module.secrets, module.parameters]
}

# Knowledge Base Stack
module "kb_stack" {
  source       = "./modules/kb-stack"
  name         = var.kb_stack_name
  kb_model_arn = var.kb_model_arn
}

# Guardrail Module
module "guardrail" {
  source                    = "./modules/bedrock-guardrails"
  guardrail_name            = "agentic-ai-guardrail"
  blocked_input_messaging   = "Your input contains content that violates our policy."
  blocked_outputs_messaging = "The response was blocked due to policy violations."
  description               = "Guardrail for agentic AI foundation"
}

# Cognito Module
module "cognito" {
  source         = "./modules/cognito"
  user_pool_name = var.user_pool_name
}

# Parameters Module (depends on KB, Guardrail, Cognito, and Gateway)
module "parameters" {
  source            = "./modules/parameters"
  knowledge_base_id = module.kb_stack.knowledge_base_id
  guardrail_id      = module.guardrail.guardrail_id
  user_pool_id      = module.cognito.user_pool_id
  client_id         = module.cognito.user_pool_client_id
  ac_stm_memory_id  = aws_bedrockagentcore_memory.agent_memory.id
  gateway_url       = aws_bedrockagentcore_gateway.cx_gateway.gateway_url
  oauth_token_url   = module.cognito.oauth_token_url

  depends_on = [
    module.kb_stack,
    module.guardrail,
    module.cognito,
    aws_bedrockagentcore_gateway.cx_gateway
  ]
}

# Secrets Module (depends on Cognito for client secret)
module "secrets" {
  source = "./modules/secrets"

  cognito_client_secret = module.cognito.client_secret

  # Placeholder values - replace with actual values
  zendesk_domain      = var.zendesk_domain
  zendesk_email       = var.zendesk_email
  zendesk_api_token   = var.zendesk_api_token
  langfuse_host       = var.langfuse_host
  langfuse_public_key = var.langfuse_public_key
  langfuse_secret_key = var.langfuse_secret_key
  gateway_url         = var.gateway_url
  gateway_api_key     = var.gateway_api_key

  depends_on = [module.cognito]
}

# ---------------------------------------------------------------------------
# Gateway Interceptor Lambda
# ---------------------------------------------------------------------------

# IAM role for the interceptor Lambda
resource "aws_iam_role" "interceptor_lambda_role" {
  name = "cx-gateway-interceptor-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "interceptor_basic" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  role       = aws_iam_role.interceptor_lambda_role.name
}

resource "aws_iam_role_policy_attachment" "interceptor_xray" {
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
  role       = aws_iam_role.interceptor_lambda_role.name
}

resource "aws_iam_role_policy" "interceptor_dlq_policy" {
  name = "interceptor-dlq-send"
  role = aws_iam_role.interceptor_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.interceptor_dlq.arn
    }]
  })
}

# DynamoDB table for rate limiting (per-caller call counters)
resource "aws_dynamodb_table" "rate_limit_table" {
  name         = "agentcore-gateway-rate-limits"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  # TTL so stale counters are automatically cleaned up
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Purpose = "AgentCore Gateway rate limiting"
  }
}

# Allow interceptor Lambda to read/write the rate limit table
resource "aws_iam_role_policy" "interceptor_dynamodb_policy" {
  name = "interceptor-dynamodb-rate-limit"
  role = aws_iam_role.interceptor_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
      ]
      Resource = aws_dynamodb_table.rate_limit_table.arn
    }]
  })
}

# Allow interceptor Lambda to call Bedrock InvokeGuardrailChecks
resource "aws_iam_role_policy" "interceptor_bedrock_policy" {
  name = "interceptor-bedrock-guardrails"
  role = aws_iam_role.interceptor_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeGuardrailChecks"]
      Resource = "*"
    }]
  })
}

# Package the interceptor source file
data "archive_file" "interceptor_lambda_zip" {
  type             = "zip"
  output_path      = "interceptor_lambda.zip"
  source_file      = "lambda/gateway_interceptor.py"
  output_file_mode = "0666"
}

# Interceptor Lambda function
resource "aws_lambda_function" "gateway_interceptor" {
  filename         = data.archive_file.interceptor_lambda_zip.output_path
  source_code_hash = data.archive_file.interceptor_lambda_zip.output_base64sha256
  function_name    = "cx-gateway-interceptor"
  role             = aws_iam_role.interceptor_lambda_role.arn
  handler          = "gateway_interceptor.lambda_handler"
  runtime          = "python3.12"
  timeout          = 10 # keep low — interceptor is on the hot path

  reserved_concurrent_executions = 100

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.interceptor_dlq.arn
  }

  environment {
    variables = {
      RATE_LIMIT_TABLE        = aws_dynamodb_table.rate_limit_table.name
      RATE_LIMIT_MAX          = tostring(var.interceptor_rate_limit_max)
      RATE_LIMIT_WINDOW       = tostring(var.interceptor_rate_limit_window)
      ENABLE_RATE_LIMIT       = tostring(var.interceptor_enable_rate_limit)
      ENABLE_GUARDRAIL_CHECKS = tostring(var.interceptor_enable_guardrail_checks)
      GUARDRAIL_BLOCK_THRESHOLD    = tostring(var.interceptor_guardrail_block_threshold)
      GUARDRAIL_ESCALATE_THRESHOLD = tostring(var.interceptor_guardrail_escalate_threshold)
    }
  }

  depends_on = [data.archive_file.interceptor_lambda_zip]
}

# Dead letter queue for interceptor Lambda
resource "aws_sqs_queue" "interceptor_dlq" {
  name                    = "cx-gateway-interceptor-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled = true
}

# Allow AgentCore Gateway to invoke the interceptor Lambda
resource "aws_lambda_permission" "allow_gateway_interceptor" {
  statement_id  = "AllowAgentCoreGatewayInterceptor"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gateway_interceptor.function_name
  principal     = "bedrock-agentcore.amazonaws.com"
  source_arn    = aws_bedrockagentcore_gateway.cx_gateway.gateway_arn
}

# ---------------------------------------------------------------------------
# Gateway IAM Role
# ---------------------------------------------------------------------------
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

resource "aws_iam_role" "gateway_role" {
  name               = "bedrock-agentcore-gateway-role"
  assume_role_policy = data.aws_iam_policy_document.gateway_assume_role.json
}

resource "aws_iam_role_policy" "gateway_policy" {
  name = "gateway-external-api-policy"
  role = aws_iam_role.gateway_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*"
      },
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.gateway_interceptor.arn,
        ]
      }
    ]
  })
}

# ---------------------------------------------------------------------------
# Bedrock AgentCore Gateway  (with interceptor attached)
# ---------------------------------------------------------------------------
resource "aws_bedrockagentcore_gateway" "cx_gateway" {
  name     = "cx-agent-gateway"
  role_arn = aws_iam_role.gateway_role.arn

  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = module.cognito.user_pool_discovery_url
      allowed_clients = [module.cognito.user_pool_client_id]
    }
  }

  protocol_type = "MCP"

  # ---------------------------------------------------------------------------
  # Interceptor configuration
  # NOTE: The exact attribute names below depend on the AWS provider version.
  # As of provider v6.47, the gateway interceptor is configured via the
  # authorizer_configuration block or a separate interceptor argument.
  # Verify against: https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrockagentcore_gateway
  #
  # If the provider does not yet expose interceptor config as a Terraform
  # attribute, attach the interceptor manually via the AWS console or CLI:
  #
  #   aws bedrock-agentcore-control update-gateway \
  #     --gateway-identifier cx-agent-gateway \
  #     --request-interceptor-lambda-arn <arn> \
  #     --response-interceptor-lambda-arn <arn> \
  #     --region us-east-1
  # ---------------------------------------------------------------------------

  depends_on = [aws_lambda_function.gateway_interceptor]
}

# Gateway Target: AgentCore Web Search Tool (managed connector)
# ---------------------------------------------------------------------------
# The AWS Terraform provider does not yet expose the connector target type.
# After terraform apply, add the Web Search target via AWS console or CLI:
#
#   aws bedrock-agentcore-control create-gateway-target \
#     --gateway-identifier <gateway-id> \
#     --name "web-search-target" \
#     --target-configuration '{"mcp":{"connector":{"connectorId":"web-search"}}}' \
#     --region us-east-1
#
# Or via the console: Gateway → Add target → Connectors → Web Search
# ---------------------------------------------------------------------------

# Deploy the endpoint
resource "aws_bedrockagentcore_agent_runtime" "agent_runtime" {
  agent_runtime_name = "langgraph_cx_agent"
  description        = "Example customer service agent for Agentic AI Foundation"
  role_arn           = module.bedrock_role.role_arn
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = module.cognito.user_pool_discovery_url
      allowed_clients = [module.cognito.user_pool_client_id]
    }
  }
  agent_runtime_artifact {
    container_configuration {
      container_uri = module.container_image.ecr_image_uri
    }
  }
  network_configuration {
    network_mode = "PUBLIC"
  }
  protocol_configuration {
    server_protocol = "HTTP"
  }
  environment_variables = {
    "AWS_REGION" = data.aws_region.current.name
    "LOG_LEVEL" = "INFO"
    "OTEL_EXPORTER_OTLP_ENDPOINT" = "${var.langfuse_host}/api/public/otel"
    "OTEL_EXPORTER_OTLP_HEADERS" = "Authorization=Basic ${base64encode("${var.langfuse_public_key}:${var.langfuse_secret_key}")}"
    "LANGSMITH_OTEL_ENABLED" = "true"
    "LANGSMITH_TRACING" = "true"
    "DISABLE_ADOT_OBSERVABILITY" = "true"
  }

}
