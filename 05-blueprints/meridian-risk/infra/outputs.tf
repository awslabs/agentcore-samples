# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

output "region" {
  description = "Region the stack is deployed into"
  value       = local.region
}

output "memory_id" {
  description = "AgentCore Memory ID (actorId = corporate customer_id)"
  value       = aws_bedrockagentcore_memory.kyc.id
}

output "memory_arn" {
  description = "AgentCore Memory ARN"
  value       = aws_bedrockagentcore_memory.kyc.arn
}

output "gateway_id" {
  description = "AgentCore Gateway ID"
  value       = aws_bedrockagentcore_gateway.kyc.gateway_id
}

output "gateway_url" {
  description = "AgentCore Gateway MCP endpoint (SigV4 authorized)"
  value       = aws_bedrockagentcore_gateway.kyc.gateway_url
}

output "runtime_arn" {
  description = "AgentCore Runtime ARN for the KYC agent"
  value       = aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_arn
}

output "runtime_id" {
  description = "AgentCore Runtime ID"
  value       = aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_id
}

output "registry_id" {
  description = "AgentCore Registry ID holding the FSI catalog"
  value       = aws_bedrockagentcore_registry.fsi.registry_id
}

output "registry_arn" {
  description = "AgentCore Registry ARN"
  value       = aws_bedrockagentcore_registry.fsi.registry_arn
}

output "kyc_tools_lambda" {
  description = "Lambda backing the Gateway's KYC tools"
  value       = aws_lambda_function.kyc_tools.function_name
}

output "policy_engine_id" {
  description = "AgentCore Policy Engine evaluating Cedar policies on gateway traffic"
  value       = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
}

output "policy_engine_mode" {
  description = "ENFORCE (violations denied) or LOG_ONLY (evaluated and logged)"
  value       = var.policy_engine_mode
}

output "guardrail_id" {
  description = "Bedrock Guardrail id enforced on the Gateway inference target"
  value       = aws_bedrock_guardrail.kyc.guardrail_id
}

output "guardrail_arn" {
  description = "Bedrock Guardrail ARN"
  value       = aws_bedrock_guardrail.kyc.guardrail_arn
}

output "guardrail_version" {
  description = "Published guardrail version referenced by the inference target"
  value       = aws_bedrock_guardrail_version.kyc.version
}

output "gateway_inference_url" {
  description = "OpenAI-compatible inference endpoint — same gateway, /inference path"
  # gateway_url ends in /mcp for the MCP transport. The inference target lives
  # under /inference/v1 on the same gateway host, so strip the /mcp suffix
  # first rather than concatenate blindly.
  value = "${trimsuffix(aws_bedrockagentcore_gateway.kyc.gateway_url, "/mcp")}/inference/v1"
}

output "demo_customers" {
  description = "Synthetic customers seeded for the demo"
  value = {
    CUST001 = "Acme Corporation Ltd — clean profile, expect APPROVE"
    CUST002 = "TechStart Innovations Inc — thin financials, expect conditional"
    CUST003 = "Global Trading Partners LLC — sanctions match + PEP + structuring, expect ESCALATE"
  }
}

# =============================================================================
# Hosted console
# =============================================================================

output "console_url" {
  description = "Amplify URL for the demo console — open this to sign in"
  value       = "https://${aws_amplify_branch.console.branch_name}.${aws_amplify_app.console.id}.amplifyapp.com"
}

output "console_api_url" {
  description = "Lambda Function URL serving the console API (streaming)"
  value       = aws_lambda_function_url.console_api.function_url
}

output "user_pool_id" {
  description = "Cognito user pool backing console sign-in"
  value       = aws_cognito_user_pool.console.id
}

output "user_pool_client_id" {
  description = "Cognito app client used by the console SPA"
  value       = aws_cognito_user_pool_client.console.id
}

output "console_username" {
  description = "Email to sign in with"
  value       = var.console_user_email == null ? "no user created" : var.console_user_email
}

output "console_password" {
  description = "Password for the console login. Read with: terraform -chdir=infra output -raw console_password"
  value       = local.console_user_password == null ? "no user created" : local.console_user_password
  sensitive   = true
}

output "identity_pool_id" {
  description = "Cognito identity pool that federates the ID token into IAM credentials for SigV4 signing"
  value       = aws_cognito_identity_pool.console.id
}

output "harness_id" {
  description = "AgentCore Harness ID — the managed agent loop (null when enable_harness = false)"
  value       = one(aws_bedrockagentcore_harness.kyc[*].harness_id)
}

output "harness_arn" {
  description = "AgentCore Harness ARN (null when enable_harness = false)"
  value       = one(aws_bedrockagentcore_harness.kyc[*].arn)
}

output "harness_skill_s3_uri" {
  description = "S3 URI of the KYC agent-skill bundle, attached to the harness at invoke time (null when enable_harness = false)"
  value       = var.enable_harness ? "s3://${aws_s3_bucket.harness_skills[0].id}/${local.harness_skill_prefix}/" : null
}
