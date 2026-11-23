# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore POC for Financial Services — KYC Onboarding Risk Assessment
#
# Deploys all four AgentCore services plus the demo console:
#   Registry  — governed catalog of agents, skills, and MCP servers
#   Gateway   — five KYC data tools exposed over MCP
#   Runtime   — multi-agent KYC orchestrator (Strands, ARM64 container)
#   Memory    — cross-session assessment history per corporate customer
# =============================================================================

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # AgentCore Runtime, Memory, and Registry names reject hyphens.
  name_underscore = replace(var.stack_name, "-", "_")

  runtime_name  = "${local.name_underscore}_kyc_agent"
  memory_name   = "${local.name_underscore}_kyc_memory"
  registry_name = "${local.name_underscore}_fsi_registry"

  common_tags = {
    Project   = var.stack_name
    ManagedBy = "Terraform"
    Purpose   = "AgentCore-FSI-POC"
  }

  repo_root = "${path.module}/.."

  # Registry is a preview API, so the seed scripts need a boto3 new enough to
  # know the bedrock-agentcore models. Prefer the repo venv (created by
  # scripts/bootstrap.sh); fall back to system python3 if it is absent.
  venv_python = "${abspath(local.repo_root)}/.venv/bin/python"
  python      = fileexists(local.venv_python) ? local.venv_python : "python3"

  # Single source of truth for the Gateway tool contract: the same spec file the
  # Lambda is documented by drives the Gateway's declared tool schemas.
  kyc_tool_specs = jsondecode(
    file("${local.repo_root}/backend/gateway/tool_spec.json")
  )
}
