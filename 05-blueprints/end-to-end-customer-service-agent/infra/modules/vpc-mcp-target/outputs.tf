output "gateway_id" {
  description = "AgentCore Gateway ID"
  value       = aws_bedrockagentcore_gateway.mcp_gateway.gateway_id
}

output "gateway_url" {
  description = "Gateway MCP endpoint URL"
  value       = aws_bedrockagentcore_gateway.mcp_gateway.gateway_url
}

output "gateway_arn" {
  description = "Gateway ARN"
  value       = aws_bedrockagentcore_gateway.mcp_gateway.gateway_arn
}

output "gateway_role_arn" {
  description = "Gateway IAM role ARN"
  value       = aws_iam_role.gateway_role.arn
}

output "entra_discovery_url" {
  description = "Configured OIDC discovery URL"
  value       = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0/.well-known/openid-configuration"
}
