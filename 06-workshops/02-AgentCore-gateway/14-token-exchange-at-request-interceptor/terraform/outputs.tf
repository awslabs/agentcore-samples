# -----------------------------------------------------------------------------
# Cognito
# -----------------------------------------------------------------------------
output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.this.id
}

output "cognito_gateway_client_id" {
  description = "Cognito App Client ID for AgentCore Gateway inbound auth"
  value       = aws_cognito_user_pool_client.gateway.id
}

output "cognito_gateway_client_secret" {
  description = "Cognito App Client Secret for AgentCore Gateway inbound auth"
  value       = aws_cognito_user_pool_client.gateway.client_secret
  sensitive   = true
}

output "cognito_downstream_client_id" {
  description = "Cognito App Client ID for downstream API Gateway auth"
  value       = aws_cognito_user_pool_client.downstream.id
}

output "cognito_downstream_client_secret" {
  description = "Cognito App Client Secret for downstream API Gateway auth"
  value       = aws_cognito_user_pool_client.downstream.client_secret
  sensitive   = true
}

output "cognito_resource_server_id" {
  description = "Cognito Resource Server identifier"
  value       = local.resource_server_id
}

output "cognito_domain" {
  description = "Cognito User Pool domain"
  value       = local.cognito_domain
}

output "cognito_token_endpoint" {
  description = "Cognito OAuth2 token endpoint"
  value       = "https://${local.cognito_domain}.auth.${local.region}.amazoncognito.com/oauth2/token"
}

# -----------------------------------------------------------------------------
# API Gateway
# -----------------------------------------------------------------------------
output "api_gateway_id" {
  description = "REST API Gateway ID"
  value       = aws_api_gateway_rest_api.this.id
}

output "api_gateway_url" {
  description = "API Gateway invoke URL"
  value       = local.api_gateway_url
}

# -----------------------------------------------------------------------------
# Lambda
# -----------------------------------------------------------------------------
output "interceptor_lambda_arn" {
  description = "Gateway Interceptor Lambda ARN"
  value       = aws_lambda_function.gateway_interceptor.arn
}

output "pre_token_lambda_arn" {
  description = "Pre Token Generation Lambda ARN"
  value       = aws_lambda_function.pre_token_generation.arn
}

# -----------------------------------------------------------------------------
# AgentCore Gateway
# -----------------------------------------------------------------------------
output "gateway_id" {
  description = "AgentCore Gateway ID"
  value       = aws_bedrockagentcore_gateway.this.gateway_id
}

output "gateway_url" {
  description = "AgentCore Gateway URL"
  value       = aws_bedrockagentcore_gateway.this.gateway_url
}

output "gateway_arn" {
  description = "AgentCore Gateway ARN"
  value       = aws_bedrockagentcore_gateway.this.gateway_arn
}

output "gateway_target_id" {
  description = "AgentCore Gateway Target ID"
  value       = aws_bedrockagentcore_gateway_target.this.target_id
}
