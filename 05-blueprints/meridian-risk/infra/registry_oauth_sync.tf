# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Registry demo: auto-populate (sync) a record over OAuth — the video's flow
#
# Instead of hand-authoring a record's tool list, the Registry calls a live MCP
# endpoint over OAuth (client-credentials, via our credential provider) and
# auto-discovers the tools into the record — and re-syncs to keep it fresh.
#
# The existing KYC Gateway is IAM-authorized, which the OAuth sync path cannot
# use, so this adds a second, OAuth-authorized (CUSTOM_JWT) MCP gateway fronting
# the SAME KYC tool Lambda. Gated by var.enable_registry_oauth_demo.
# =============================================================================

# -----------------------------------------------------------------------------
# OAuth-authorized (CUSTOM_JWT) MCP gateway over the existing KYC tool Lambda
#
# Reuses aws_iam_role.gateway (already allowed to invoke the Lambda) and the
# aws_lambda_function.kyc_tools handler. No policy engine attached, so the
# metadata_configuration provider quirk that the SigV4 gateway hits does not
# apply here.
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_gateway" "kyc_oauth" {
  count       = var.enable_registry_oauth_demo ? 1 : 0
  name        = "${var.stack_name}-oauth-gateway"
  role_arn    = aws_iam_role.gateway.arn
  description = "OAuth-authorized (JWT) MCP gateway fronting the KYC tools; sync source for the Registry auto-populate demo"

  protocol_type = "MCP"
  protocol_configuration {
    mcp {
      supported_versions = ["2025-03-26"]
    }
  }

  # Inbound OAuth: the Registry's sync calls this with a client-credentials token
  # minted for the M2M client. allowed_clients validates the token's client_id.
  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = local.cognito_discovery_url
      allowed_clients = [aws_cognito_user_pool_client.oauth_demo_m2m[0].id]
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "kyc_oauth_tools" {
  count              = var.enable_registry_oauth_demo ? 1 : 0
  name               = "kyc-tools"
  gateway_identifier = aws_bedrockagentcore_gateway.kyc_oauth[0].gateway_id
  description        = "Five KYC data-retrieval tools backed by one Lambda (OAuth gateway)"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.kyc_tools.arn

        tool_schema {
          dynamic "inline_payload" {
            for_each = local.kyc_tool_specs
            content {
              name        = inline_payload.value.name
              description = inline_payload.value.description

              input_schema {
                type        = inline_payload.value.inputSchema.type
                description = inline_payload.value.inputSchema.description

                dynamic "property" {
                  for_each = inline_payload.value.inputSchema.properties
                  content {
                    name        = property.key
                    type        = property.value.type
                    description = property.value.description
                    required = contains(
                      inline_payload.value.inputSchema.required, property.key
                    )
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [aws_bedrockagentcore_gateway.kyc_oauth]
}

# -----------------------------------------------------------------------------
# URL-synchronized MCP record — the Registry auto-populates its tools over OAuth
# -----------------------------------------------------------------------------

resource "null_resource" "sync_record" {
  count = var.enable_registry_oauth_demo ? 1 : 0

  triggers = {
    registry_id  = aws_bedrockagentcore_registry.fsi.registry_id
    mcp_url      = aws_bedrockagentcore_gateway.kyc_oauth[0].gateway_url
    provider_arn = "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:token-vault/default/oauth2credentialprovider/${local.oauth_provider_name}"
    seed_hash    = filesha256("${local.repo_root}/scripts/seed_sync_record.py")

    # Captured for the destroy provisioner, which may only read self.triggers.
    region      = local.region
    python      = local.python
    seed_script = "${local.repo_root}/scripts/seed_sync_record.py"
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/seed_sync_record.py" \
        --registry-id "${aws_bedrockagentcore_registry.fsi.registry_id}" \
        --mcp-url "${aws_bedrockagentcore_gateway.kyc_oauth[0].gateway_url}" \
        --provider-arn "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:token-vault/default/oauth2credentialprovider/${local.oauth_provider_name}" \
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
    aws_bedrockagentcore_gateway_target.kyc_oauth_tools,
    null_resource.oauth_provider,
    aws_bedrockagentcore_registry.fsi,
  ]
}

output "oauth_sync_demo" {
  description = "Wiring for the OAuth auto-populate (sync) demo. Null unless enable_registry_oauth_demo is true."
  value = var.enable_registry_oauth_demo ? {
    oauth_gateway_url  = aws_bedrockagentcore_gateway.kyc_oauth[0].gateway_url
    synced_record_name = "kyc-tools-oauth-synced"
    registry_id        = aws_bedrockagentcore_registry.fsi.registry_id
    region             = local.region
  } : null
}
