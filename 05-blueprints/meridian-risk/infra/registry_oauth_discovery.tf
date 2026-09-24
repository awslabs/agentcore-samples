# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Registry demo: discovery over OAuth (a JWT-authorized registry)
#
# A registry's inbound auth type is immutable and single-valued (IAM *or* JWT,
# never both), and the AWS SDK/CLI can only talk to an IAM registry (they always
# SigV4-sign). So to demonstrate OAuth-authenticated *discovery*, this stands up
# a SECOND, CUSTOM_JWT registry alongside the IAM one (which the console + seed
# scripts keep using). Its discoverable data-plane API (SearchRegistryRecords)
# and MCP endpoint require an OAuth bearer token instead of SigV4.
#
# Admin CRUDL (create/approve records) is IAM-authorized regardless of the
# registry's inbound auth, so records are still seeded with boto3.
# Gated by var.enable_registry_oauth_demo.
# =============================================================================

resource "aws_bedrockagentcore_registry" "fsi_oauth" {
  count       = var.enable_registry_oauth_demo ? 1 : 0
  name        = "${local.name_underscore}_fsi_oauth_registry"
  description = "JWT/OAuth-authorized registry — discovery (search) requires an OAuth bearer token, not IAM/SigV4"

  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = local.cognito_discovery_url
      allowed_clients = [aws_cognito_user_pool_client.oauth_demo_m2m[0].id]
    }
  }

  # Auto-approve so a seeded record is immediately discoverable in the demo.
  approval_configuration {
    auto_approval = true
  }
}

# Seed one record so there is something to discover. Reuses seed_oauth_record.py
# against this registry — admin CRUDL is IAM-based, so seeding is unchanged.
resource "null_resource" "oauth_discovery_record" {
  count = var.enable_registry_oauth_demo ? 1 : 0

  triggers = {
    registry_id = aws_bedrockagentcore_registry.fsi_oauth[0].registry_id
    twin_arn    = aws_bedrockagentcore_agent_runtime.kyc_oauth[0].agent_runtime_arn
    token_url   = local.cognito_token_url
    seed_hash   = filesha256("${local.repo_root}/scripts/seed_oauth_record.py")

    region      = local.region
    python      = local.python
    seed_script = "${local.repo_root}/scripts/seed_oauth_record.py"
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/seed_oauth_record.py" \
        --registry-id "${aws_bedrockagentcore_registry.fsi_oauth[0].registry_id}" \
        --twin-runtime-arn "${aws_bedrockagentcore_agent_runtime.kyc_oauth[0].agent_runtime_arn}" \
        --token-url "${local.cognito_token_url}" \
        --region "${local.region}"
    EOT
  }

  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command = join(" ", [
      self.triggers.python, self.triggers.seed_script,
      "--delete",
      "--registry-id", self.triggers.registry_id,
      "--region", self.triggers.region,
    ])
  }

  depends_on = [
    aws_bedrockagentcore_registry.fsi_oauth,
    aws_bedrockagentcore_agent_runtime.kyc_oauth,
  ]
}

output "oauth_discovery_demo" {
  description = "Wiring for the OAuth-discovery (JWT registry) demo. Null unless enable_registry_oauth_demo is true."
  value = var.enable_registry_oauth_demo ? {
    registry_arn = aws_bedrockagentcore_registry.fsi_oauth[0].registry_arn
    registry_id  = aws_bedrockagentcore_registry.fsi_oauth[0].registry_id
    record_name  = "kyc-orchestrator-oauth"
    # SDK/CLI can't sign a JWT registry; call these directly with a Bearer token.
    search_url = "https://bedrock-agentcore.${local.region}.amazonaws.com/registry-records/search"
    mcp_url    = "https://bedrock-agentcore.${local.region}.amazonaws.com/registry/${aws_bedrockagentcore_registry.fsi_oauth[0].registry_id}/mcp"
    region     = local.region
  } : null
}
