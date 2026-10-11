# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Registry demo: discover an agent, then invoke it directly via OAuth
#
# Shows the consumer side of AWS Agent Registry: a caller searches the registry
# for an agent, reads its endpoint + OAuth requirement from the record, mints a
# machine-to-machine OAuth access token through AgentCore Identity, and calls the
# agent with `Authorization: Bearer` — no SigV4 on the agent call.
#
# The whole slice is gated by var.enable_registry_oauth_demo (default off) so a
# normal deploy is unchanged. It reuses the existing agent container image and
# execution role; only the inbound auth (a JWT authorizer) differs from the
# console-facing runtime, whose SigV4 path is left untouched.
# =============================================================================

locals {
  # Cognito user-pool OIDC endpoints (the pool itself lives in cognito.tf).
  cognito_discovery_url = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.console.id}/.well-known/openid-configuration"
  # Token endpoint is only meaningful once the hosted-UI domain exists.
  cognito_token_url = var.enable_registry_oauth_demo ? "https://${aws_cognito_user_pool_domain.oauth_demo[0].domain}.auth.${local.region}.amazoncognito.com/oauth2/token" : ""
  # The credential provider name is fixed (we own it), so IAM/outputs can
  # reference it without depending on the script that creates the provider.
  # Distinct from the old inline-secret provider name so this apply can delete
  # that one (TF-managed) while the script creates this one — no name collision.
  oauth_provider_name = "${local.name_underscore}_kyc_oauth_ext"
}

# -----------------------------------------------------------------------------
# Cognito machine-to-machine (client_credentials) setup
# -----------------------------------------------------------------------------

# Hosted-UI domain — required for the /oauth2/token endpoint the client_credentials
# grant uses. The prefix must be globally unique within the region; the account id
# suffix keeps it collision-free.
resource "aws_cognito_user_pool_domain" "oauth_demo" {
  count        = var.enable_registry_oauth_demo ? 1 : 0
  domain       = "${var.stack_name}-oauth-${local.account_id}"
  user_pool_id = aws_cognito_user_pool.console.id
}

# Resource server declares the scope the agent call requires. scope_identifiers
# reads back as ["kyc-agent/invoke"].
resource "aws_cognito_resource_server" "oauth_demo" {
  count        = var.enable_registry_oauth_demo ? 1 : 0
  identifier   = "kyc-agent"
  name         = "${var.stack_name}-kyc-agent"
  user_pool_id = aws_cognito_user_pool.console.id

  scope {
    scope_name        = "invoke"
    scope_description = "Invoke the KYC agent runtime"
  }
}

# Confidential machine client — client_credentials only, no user-facing flows.
# A secret is mandatory for client_credentials, so generate_secret = true.
resource "aws_cognito_user_pool_client" "oauth_demo_m2m" {
  count           = var.enable_registry_oauth_demo ? 1 : 0
  name            = "${var.stack_name}-oauth-m2m"
  user_pool_id    = aws_cognito_user_pool.console.id
  generate_secret = true

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = aws_cognito_resource_server.oauth_demo[0].scope_identifiers
  supported_identity_providers         = ["COGNITO"]

  # The domain must exist before a client can use OAuth flows against it.
  depends_on = [aws_cognito_user_pool_domain.oauth_demo]
}

# -----------------------------------------------------------------------------
# AgentCore Identity — OAuth credential provider with a CUSTOMER-OWNED secret
#
# The client secret lives in a Secrets Manager secret WE create, and the
# credential provider references it via clientSecretSource=EXTERNAL. Terraform's
# provider (6.58) can't express EXTERNAL for the CustomOauth2 vendor, so the
# provider is created by scripts/manage_oauth_provider.py through a null_resource
# — the same pattern used for Registry records and the Gateway inference target.
#
# Why: keeps the client secret out of Terraform state (the only remaining copy is
# on Cognito's own client resource, unavoidable for a Cognito-generated secret)
# and gives us a secret we can rotate. AgentCore reads OUR secret at token time.
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "oauth_client" {
  count       = var.enable_registry_oauth_demo ? 1 : 0
  name        = "${var.stack_name}/oauth-m2m-client"
  description = "Cognito M2M client secret for the Registry OAuth demo (owned by us; referenced by the AgentCore credential provider via EXTERNAL)"
}

# The script writes the secret value AND creates the EXTERNAL-secret provider.
resource "null_resource" "oauth_provider" {
  count = var.enable_registry_oauth_demo ? 1 : 0

  triggers = {
    provider_name = local.oauth_provider_name
    client_id     = aws_cognito_user_pool_client.oauth_demo_m2m[0].id
    secret_arn    = aws_secretsmanager_secret.oauth_client[0].arn
    user_pool_id  = aws_cognito_user_pool.console.id
    discovery_url = local.cognito_discovery_url
    script_hash   = filesha256("${local.repo_root}/scripts/manage_oauth_provider.py")

    # Captured for the destroy provisioner, which may only read self.triggers.
    region = local.region
    python = local.python
    script = "${local.repo_root}/scripts/manage_oauth_provider.py"
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/manage_oauth_provider.py" \
        --name "${local.oauth_provider_name}" \
        --region "${local.region}" \
        --user-pool-id "${aws_cognito_user_pool.console.id}" \
        --client-id "${aws_cognito_user_pool_client.oauth_demo_m2m[0].id}" \
        --secret-arn "${aws_secretsmanager_secret.oauth_client[0].arn}" \
        --discovery-url "${local.cognito_discovery_url}"
    EOT
  }

  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command = join(" ", [
      self.triggers.python, self.triggers.script,
      "--delete",
      "--name", self.triggers.provider_name,
      "--region", self.triggers.region,
    ])
  }

  depends_on = [
    aws_secretsmanager_secret.oauth_client,
    aws_cognito_user_pool_client.oauth_demo_m2m,
  ]
}

# Workload identity for the consumer — GetResourceOauth2Token requires a
# workloadIdentityToken, obtained from GetWorkloadAccessToken(workloadName=...).
resource "aws_bedrockagentcore_workload_identity" "oauth_demo_consumer" {
  count = var.enable_registry_oauth_demo ? 1 : 0
  name  = "${local.name_underscore}_kyc_oauth_consumer"
}

# -----------------------------------------------------------------------------
# OAuth-invocable agent — a twin of the KYC runtime with a JWT authorizer
#
# Reuses the same image and execution role as aws_bedrockagentcore_agent_runtime.kyc.
# A runtime has a single authorizer, so rather than switch the console-facing
# runtime to JWT (which would break its SigV4 path), this twin carries the JWT
# authorizer and is the endpoint the OAuth consumer calls.
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "kyc_oauth" {
  count              = var.enable_registry_oauth_demo ? 1 : 0
  agent_runtime_name = "${local.name_underscore}_kyc_oauth_agent"
  role_arn           = aws_iam_role.runtime.arn
  description        = "OAuth-invocable twin of the KYC runtime (JWT authorizer) for the Registry discover-then-invoke demo"

  agent_runtime_artifact {
    container_configuration {
      container_uri = module.agent_image.image_uri
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  # Inbound OAuth: accept a bearer JWT minted for the M2M client by Cognito.
  # allowed_clients validates the token's client_id claim. Cognito M2M access
  # tokens carry client_id + scope (no aud), so no allowed_audience is set.
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url   = local.cognito_discovery_url
      allowed_clients = [aws_cognito_user_pool_client.oauth_demo_m2m[0].id]
    }
  }

  environment_variables = {
    AWS_REGION        = local.region
    MEMORY_ID         = aws_bedrockagentcore_memory.kyc.id
    GATEWAY_URL       = aws_bedrockagentcore_gateway.kyc.gateway_url
    MODEL_ID          = var.model_id
    STACK_NAME        = var.stack_name
    LOG_LEVEL         = "INFO"
    INFERENCE_ROUTE   = var.inference_route
    GATEWAY_MODEL_ID  = var.gateway_model_id
    GUARDRAIL_ID      = aws_bedrock_guardrail.kyc.guardrail_id
    GUARDRAIL_VERSION = aws_bedrock_guardrail_version.kyc.version
    POLICY_ENGINE_ID  = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
    POLICY_MODE       = var.policy_engine_mode
  }

  depends_on = [
    aws_iam_role_policy.runtime,
    module.agent_image,
    aws_bedrockagentcore_gateway_target.kyc_tools,
  ]
}

# -----------------------------------------------------------------------------
# Least-privilege consumer role
#
# The demo consumer assumes this role, which holds exactly the actions the
# discover-then-invoke flow needs — nothing more. Trusts the account so the
# operator running the script (or CI) can assume it.
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "oauth_consumer_assume" {
  count = var.enable_registry_oauth_demo ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_iam_role" "oauth_consumer" {
  count              = var.enable_registry_oauth_demo ? 1 : 0
  name               = "${var.stack_name}-oauth-consumer-role"
  assume_role_policy = data.aws_iam_policy_document.oauth_consumer_assume[0].json
  description        = "Least-privilege role the OAuth-demo consumer assumes to discover a record and mint an M2M token"
}

data "aws_iam_policy_document" "oauth_consumer" {
  count = var.enable_registry_oauth_demo ? 1 : 0

  statement {
    sid       = "DiscoverRecords"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:SearchRegistryRecords", "bedrock-agentcore:GetRegistryRecord"]
    resources = ["*"]
  }

  statement {
    sid     = "WorkloadToken"
    effect  = "Allow"
    actions = ["bedrock-agentcore:GetWorkloadAccessToken"]
    resources = [
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*",
    ]
  }

  # GetResourceOauth2Token authorizes against a CHAIN of resources, revealed one
  # per attempt (verified live) — the caller's workload-identity, the token vault,
  # and the specific credential provider. Scoping to only the provider fails.
  statement {
    sid     = "OAuthToken"
    effect  = "Allow"
    actions = ["bedrock-agentcore:GetResourceOauth2Token"]
    resources = [
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:workload-identity-directory/default/workload-identity/*",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:token-vault/default",
      "arn:aws:bedrock-agentcore:${local.region}:${local.account_id}:token-vault/default/oauth2credentialprovider/${local.oauth_provider_name}",
    ]
  }

  # GetResourceOauth2Token reads the provider's client secret using the CALLER's
  # identity (forward-access session). With clientSecretSource=EXTERNAL that is
  # OUR secret; the bedrock-agentcore-identity!… pattern is kept defensively in
  # case the service also stages a managed copy.
  statement {
    sid     = "ReadProviderSecret"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.oauth_client[0].arn,
      "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:bedrock-agentcore-identity!default/oauth2/${local.oauth_provider_name}-*",
    ]
  }
}

resource "aws_iam_role_policy" "oauth_consumer" {
  count  = var.enable_registry_oauth_demo ? 1 : 0
  name   = "${var.stack_name}-oauth-consumer-policy"
  role   = aws_iam_role.oauth_consumer[0].id
  policy = data.aws_iam_policy_document.oauth_consumer[0].json
}

# -----------------------------------------------------------------------------
# A2A registry record for the OAuth-invocable agent
#
# Records have no Terraform resource (see docs/preview-api-notes.md), so this is
# seeded by a script the same way as registry.tf's records. The record's agent
# card advertises the OAuth2 client-credentials scheme so a consumer discovers
# both the endpoint and how to authenticate to it.
# -----------------------------------------------------------------------------

resource "null_resource" "oauth_record" {
  count = var.enable_registry_oauth_demo ? 1 : 0

  triggers = {
    registry_id = aws_bedrockagentcore_registry.fsi.registry_id
    twin_arn    = aws_bedrockagentcore_agent_runtime.kyc_oauth[0].agent_runtime_arn
    token_url   = local.cognito_token_url
    seed_hash   = filesha256("${local.repo_root}/scripts/seed_oauth_record.py")

    # Captured for the destroy provisioner, which may only read self.triggers.
    region      = local.region
    python      = local.python
    seed_script = "${local.repo_root}/scripts/seed_oauth_record.py"
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/seed_oauth_record.py" \
        --registry-id "${aws_bedrockagentcore_registry.fsi.registry_id}" \
        --twin-runtime-arn "${aws_bedrockagentcore_agent_runtime.kyc_oauth[0].agent_runtime_arn}" \
        --token-url "${local.cognito_token_url}" \
        --region "${local.region}"
    EOT
  }

  # Best-effort removal of the record on destroy so the registry can be deleted.
  # The seed script owns deletion of its own record (--delete).
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
    aws_bedrockagentcore_agent_runtime.kyc_oauth,
    aws_bedrockagentcore_registry.fsi,
  ]
}

# -----------------------------------------------------------------------------
# Outputs consumed by scripts/discover_and_invoke_via_oauth.py
# -----------------------------------------------------------------------------

output "oauth_demo" {
  description = "Wiring for the discover-then-invoke-via-OAuth consumer. Null unless enable_registry_oauth_demo is true."
  value = var.enable_registry_oauth_demo ? {
    registry_arn      = aws_bedrockagentcore_registry.fsi.registry_arn
    registry_id       = aws_bedrockagentcore_registry.fsi.registry_id
    record_name       = "kyc-orchestrator-oauth"
    provider_name     = local.oauth_provider_name
    owned_secret_arn  = aws_secretsmanager_secret.oauth_client[0].arn
    workload_name     = aws_bedrockagentcore_workload_identity.oauth_demo_consumer[0].name
    consumer_role_arn = aws_iam_role.oauth_consumer[0].arn
    scope             = "kyc-agent/invoke"
    twin_runtime_arn  = aws_bedrockagentcore_agent_runtime.kyc_oauth[0].agent_runtime_arn
    token_url         = local.cognito_token_url
    region            = local.region
  } : null
}
