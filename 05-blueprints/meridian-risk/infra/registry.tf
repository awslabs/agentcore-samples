# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore Registry (preview)
#
# The private, governed catalog of this organization's AI resources. The
# registry itself is Terraform-managed; registry *records* have no Terraform
# resource yet, so they are provisioned by scripts/seed_registry.py during
# apply and torn down on destroy.
# =============================================================================

# NOTE: terraform validate warns that this resource is deprecated as of
# 2026-09-17. That is expected — Registry is still in preview and the AWS
# provider has not yet shipped a replacement resource. Revisit when one lands.
resource "aws_bedrockagentcore_registry" "fsi" {
  name        = local.registry_name
  description = "Governed catalog of FSI agents, agent skills, and MCP servers"

  # Consumers of the Search and Invoke APIs authorize with IAM credentials.
  # (This does not affect the admin CRUDL APIs used to manage records.)
  authorizer_type = "AWS_IAM"

  approval_configuration {
    # Left disabled so the demo can walk through the governance workflow:
    # DRAFT -> PENDING_APPROVAL -> APPROVED.
    auto_approval = var.registry_auto_approval
  }
}

# -----------------------------------------------------------------------------
# Registry records
#
# Seeds four records describing the deployed system:
#   MCP          — the KYC Gateway and its five tools
#   A2A          — the KYC orchestrator's agent card
#   AGENT_SKILLS — credit-risk-analysis, aml-compliance-screening
#
# Re-runs when the seed script or the Gateway's tool contract changes.
# -----------------------------------------------------------------------------

resource "null_resource" "registry_records" {
  triggers = {
    registry_id   = aws_bedrockagentcore_registry.fsi.registry_id
    seed_script   = filesha256("${local.repo_root}/scripts/seed_registry.py")
    tool_spec     = filesha256("${local.repo_root}/backend/gateway/tool_spec.json")
    gateway_url   = aws_bedrockagentcore_gateway.kyc.gateway_url
    runtime_arn   = aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_arn
    auto_approval = var.registry_auto_approval

    # Captured for the destroy provisioner, which may only read self.triggers.
    region       = local.region
    purge_script = "${local.repo_root}/scripts/purge_registry.py"
    python       = local.python
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/seed_registry.py" \
        --registry-id "${aws_bedrockagentcore_registry.fsi.registry_id}" \
        --gateway-url "${aws_bedrockagentcore_gateway.kyc.gateway_url}" \
        --gateway-arn "${aws_bedrockagentcore_gateway.kyc.gateway_arn}" \
        --runtime-arn "${aws_bedrockagentcore_agent_runtime.kyc.agent_runtime_arn}" \
        --tool-spec "${local.repo_root}/backend/gateway/tool_spec.json" \
        --region "${local.region}"
    EOT
  }

  # Records must be removed before the registry can be deleted.
  provisioner "local-exec" {
    when       = destroy
    on_failure = continue
    command = join(" ", [
      self.triggers.python, self.triggers.purge_script,
      "--registry-id", self.triggers.registry_id,
      "--region", self.triggers.region,
    ])
  }

  depends_on = [
    aws_bedrockagentcore_registry.fsi,
    aws_bedrockagentcore_gateway_target.kyc_tools,
    aws_bedrockagentcore_agent_runtime.kyc,
  ]
}
