# =============================================================================
# API Key Credential Provider (required by OpenAPI targets; actual auth is
# handled by the interceptor which injects the Cognito JWT)
# =============================================================================
resource "aws_bedrockagentcore_api_key_credential_provider" "this" {
  name    = "api-key-provider-${local.suffix}"
  api_key = "placeholder-not-used-for-auth"
}

# =============================================================================
# IAM Role for AgentCore Gateway
# =============================================================================
resource "aws_iam_role" "gateway" {
  name = "AgentCoreGatewayRole-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock-agentcore.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "gateway_agentcore" {
  role       = aws_iam_role.gateway.name
  policy_arn = "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess"
}

resource "aws_iam_role_policy" "gateway_lambda_invoke" {
  name = "LambdaInvokePolicy"
  role = aws_iam_role.gateway.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeAsync", "lambda:InvokeFunction"]
      Resource = "*"
    }]
  })
}

# =============================================================================
# AgentCore Gateway
# =============================================================================
resource "aws_bedrockagentcore_gateway" "this" {
  name        = "${var.name_prefix}-gateway-${local.suffix}"
  description = "AgentCore Gateway with Cognito 2LO auth - ${local.suffix}"
  role_arn    = aws_iam_role.gateway.arn

  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.this.id}/.well-known/openid-configuration"
      allowed_clients = [aws_cognito_user_pool_client.gateway.id]
      allowed_scopes  = [
        "${local.resource_server_id}/read",
        "${local.resource_server_id}/write",
      ]
    }
  }

  protocol_type = "MCP"
  protocol_configuration {
    mcp {
      supported_versions = ["2025-03-26", "2025-06-18"]
    }
  }

  interceptor_configuration {
    interception_points = ["REQUEST"]

    interceptor {
      lambda {
        arn = aws_lambda_function.gateway_interceptor.arn
      }
    }

    input_configuration {
      pass_request_headers = true
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.gateway_agentcore,
    aws_iam_role_policy.gateway_lambda_invoke,
  ]
}

# =============================================================================
# AgentCore Gateway Target (OpenAPI — clean spec, no API Gateway extensions)
# =============================================================================
resource "aws_bedrockagentcore_gateway_target" "this" {
  name               = "posts-api-target-${local.suffix}"
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id

  credential_provider_configuration {
    api_key {
      provider_arn              = aws_bedrockagentcore_api_key_credential_provider.this.credential_provider_arn
      credential_parameter_name = "X-Api-Key"
      credential_location       = "HEADER"
    }
  }

  target_configuration {
    mcp {
      open_api_schema {
        inline_payload {
          payload = jsonencode(local.target_openapi_spec)
        }
      }
    }
  }
}

# Clean OpenAPI spec for the AgentCore target — no x-amazon-apigateway-*
# extensions, just standard OpenAPI that AgentCore converts into MCP tools.
locals {
  target_openapi_spec = {
    openapi = "3.0.1"
    info = {
      title       = "Posts API"
      version     = "1.0.0"
      description = "Create and manage posts"
    }
    servers = [{
      url         = local.api_gateway_url
      description = "Posts API Gateway endpoint"
    }]
    components = {
      schemas = local.schemas
    }
    paths = {
      "/posts" = {
        post = {
          summary     = "Create a new post"
          operationId = "createPost"
          requestBody = local.create_post_request_body
          responses   = local.create_post_responses
        }
      }
    }
  }
}
