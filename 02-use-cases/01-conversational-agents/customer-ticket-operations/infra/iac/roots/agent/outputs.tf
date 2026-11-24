locals {
  outputs = {
    runtime_arn  = aws_bedrockagentcore_agent_runtime.agent.agent_runtime_arn
    gateway_url  = aws_bedrockagentcore_gateway.build["v0"].gateway_url
    model_id     = var.model_id
    gateway_log  = aws_cloudwatch_log_group.gateway["v0"].name
    gateways     = jsonencode({ for k, g in aws_bedrockagentcore_gateway.build : k => g.gateway_id })
    gateway_urls = jsonencode({ for k, g in aws_bedrockagentcore_gateway.build : k => g.gateway_url })
    gateway_logs = jsonencode({ for k, g in aws_cloudwatch_log_group.gateway : k => g.name })
    # build -> policy engine id (V2 shares V1's gateway, V5 shares V4's)
    policy_engines = jsonencode({ for b, g in { v1 = "v1", v2 = "v1", v3 = "v3", v4 = "v4", v5 = "v4" } : b => aws_bedrockagentcore_policy_engine.build[g].policy_engine_id })
  }
}

# Read by the API Lambda at request time (the API is deployed before this root).
resource "aws_ssm_parameter" "agent" {
  for_each = local.outputs
  name     = "${local.ssm}/agent/${each.key}"
  type     = "String"
  value    = each.value
}

output "agent" {
  value = local.outputs
}
