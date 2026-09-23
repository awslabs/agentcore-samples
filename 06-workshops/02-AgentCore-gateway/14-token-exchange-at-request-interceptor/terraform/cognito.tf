# -----------------------------------------------------------------------------
# Cognito User Pool
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  name = "${var.name_prefix}-pool-${local.suffix}"

  password_policy {
    minimum_length    = 8
    require_uppercase = false
    require_lowercase = false
    require_numbers   = false
    require_symbols   = false
  }
}

# Upgrade to Essentials tier (required for V3_0 Pre Token Generation).
# Then attach the Pre Token Generation Lambda trigger.
# The aws_cognito_user_pool resource does not support UserPoolTier natively,
# and the V3_0 trigger requires Essentials tier, so both are done via CLI.
resource "null_resource" "configure_user_pool" {
  depends_on = [
    aws_cognito_user_pool.this,
    aws_lambda_function.pre_token_generation,
    aws_lambda_permission.cognito_pre_token,
  ]

  triggers = {
    user_pool_id = aws_cognito_user_pool.this.id
    lambda_arn   = aws_lambda_function.pre_token_generation.arn
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws cognito-idp update-user-pool \
        --user-pool-id ${aws_cognito_user_pool.this.id} \
        --user-pool-tier ESSENTIALS \
        --region ${local.region}

      sleep 5

      aws cognito-idp update-user-pool \
        --user-pool-id ${aws_cognito_user_pool.this.id} \
        --lambda-config '{"PreTokenGeneration":"${aws_lambda_function.pre_token_generation.arn}","PreTokenGenerationConfig":{"LambdaVersion":"V3_0","LambdaArn":"${aws_lambda_function.pre_token_generation.arn}"}}' \
        --region ${local.region}
    EOT
  }
}

# -----------------------------------------------------------------------------
# Cognito User Pool Domain
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.cognito_domain
  user_pool_id = aws_cognito_user_pool.this.id
}

# -----------------------------------------------------------------------------
# Cognito Resource Server
# -----------------------------------------------------------------------------
resource "aws_cognito_resource_server" "this" {
  identifier   = local.resource_server_id
  name         = "AgentCore API ${local.suffix}"
  user_pool_id = aws_cognito_user_pool.this.id

  scope {
    scope_name        = "read"
    scope_description = "Read access to AgentCore Gateway"
  }

  scope {
    scope_name        = "write"
    scope_description = "Write access to AgentCore Gateway"
  }
}

# -----------------------------------------------------------------------------
# Cognito App Client - Gateway (inbound auth to AgentCore Gateway)
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool_client" "gateway" {
  name         = "${var.name_prefix}-gateway-client-${local.suffix}"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  supported_identity_providers         = ["COGNITO"]

  allowed_oauth_scopes = [
    "${local.resource_server_id}/read",
    "${local.resource_server_id}/write",
  ]

  depends_on = [aws_cognito_resource_server.this]
}

# -----------------------------------------------------------------------------
# Cognito App Client - Downstream (used by interceptor for API Gateway auth)
# -----------------------------------------------------------------------------
resource "aws_cognito_user_pool_client" "downstream" {
  name         = "${var.name_prefix}-downstream-client-${local.suffix}"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  supported_identity_providers         = ["COGNITO"]

  allowed_oauth_scopes = [
    "${local.resource_server_id}/read",
    "${local.resource_server_id}/write",
  ]

  depends_on = [aws_cognito_resource_server.this]
}
