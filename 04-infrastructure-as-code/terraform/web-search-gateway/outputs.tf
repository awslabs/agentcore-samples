locals {
  gateway_url_trimmed = trimsuffix(aws_bedrockagentcore_gateway.web_search.gateway_url, "/")

  # Normalize to the MCP endpoint. The control plane's `gatewayUrl` already ends
  # in /mcp in most cases, but the related `endpoint` field does not — so guard
  # rather than assume, and avoid a 404 mid-demo.
  mcp_endpoint = endswith(local.gateway_url_trimmed, "/mcp") ? local.gateway_url_trimmed : "${local.gateway_url_trimmed}/mcp"
}

output "gateway_id" {
  description = "Gateway identifier."
  value       = aws_bedrockagentcore_gateway.web_search.gateway_id
}

output "gateway_arn" {
  description = "Gateway ARN. This is the resource callers need InvokeGateway on."
  value       = aws_bedrockagentcore_gateway.web_search.gateway_arn
}

output "gateway_url" {
  description = "Raw Gateway URL as returned by the service."
  value       = aws_bedrockagentcore_gateway.web_search.gateway_url
}

output "mcp_endpoint" {
  description = "Gateway MCP endpoint to POST tools/list and tools/call to. Normalized to end in /mcp."
  value       = local.mcp_endpoint
}

output "target_id" {
  description = "Web Search gateway target identifier."
  value       = aws_bedrockagentcore_gateway_target.web_search.target_id
}

output "tool_name" {
  description = "The tool name the calling agent sees over MCP (<target_name>___WebSearch)."
  value       = local.tool_name
}

output "gateway_service_role_arn" {
  description = "Gateway service role (outbound auth to the Web Search backend)."
  value       = aws_iam_role.gateway_service.arn
}

output "demo_caller_role_arn" {
  description = "Least-privilege role to assume when calling the Gateway."
  value       = aws_iam_role.demo_caller.arn
}

output "connector_version" {
  description = "Pinned Web Search connector version."
  value       = var.connector_version
}

output "domain_governance" {
  description = "Target-level domain filters in effect. Hidden from the calling agent and applied to every request."
  value = {
    include = var.domain_include
    exclude = var.domain_exclude
    active  = length(local.domain_filter) > 0
  }
}

output "demo_command" {
  description = "Ready-to-run command that proves Web Search works end to end."
  value       = "python3 scripts/confirm-web-search.py --query \"What are the top AI news headlines today?\""
}
