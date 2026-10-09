# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

variable "stack_name" {
  description = "Base name for all resources. Used as a prefix for resource naming."
  type        = string
  default     = "kyc-agentcore"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,26}$", var.stack_name))
    error_message = "Stack name must start with a lowercase letter, be 3-27 characters, and contain only lowercase alphanumerics and hyphens."
  }
}

variable "aws_region" {
  description = "AWS region. AgentCore Registry preview is available in us-east-1, us-west-2, ap-northeast-1, ap-southeast-2, and eu-west-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition = contains(
      ["us-east-1", "us-west-2", "ap-northeast-1", "ap-southeast-2", "eu-west-1"],
      var.aws_region
    )
    error_message = "Region must be one where AgentCore Registry (preview) is available."
  }
}

variable "model_id" {
  description = "Bedrock model ID used by the agents. Cross-region inference profile recommended for throughput."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "memory_event_expiry_days" {
  description = "Retention window for short-term AgentCore Memory events."
  type        = number
  default     = 30

  validation {
    condition     = var.memory_event_expiry_days >= 7 && var.memory_event_expiry_days <= 365
    error_message = "Event expiry must be between 7 and 365 days."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 7
}

variable "console_user_email" {
  description = "Email address for the demo console login. Becomes the Cognito username. Set to null to skip user creation."
  type        = string
  default     = null

  validation {
    condition     = var.console_user_email == null || can(regex("^[^@\\s]+@[^@\\s]+\\.[a-zA-Z]{2,}$", var.console_user_email))
    error_message = "Must be a valid email address or null."
  }
}

variable "console_user_password" {
  description = "Password for the demo console login. Leave null to generate one (surfaced via `terraform output -raw console_password`). Must satisfy the pool policy: 12+ chars with upper, lower, number, and symbol."
  type        = string
  default     = null
  sensitive   = true
}

variable "registry_auto_approval" {
  description = "When true, Registry records are approved automatically. Left false so the demo can show the governance workflow (DRAFT -> PENDING_APPROVAL -> APPROVED)."
  type        = bool
  default     = false
}

variable "policy_engine_mode" {
  description = "How the Policy Engine treats policy violations on gateway traffic. \"ENFORCE\" denies them; \"LOG_ONLY\" evaluates and logs without blocking. ENFORCE is the default because enforcement is the point of the POC, but switch to LOG_ONLY before adding or widening a policy: ENFORCE is default-deny, so a statement that references a context field the gateway does not populate can deny traffic that used to work."
  type        = string
  default     = "ENFORCE"
  validation {
    condition     = contains(["LOG_ONLY", "ENFORCE"], var.policy_engine_mode)
    error_message = "policy_engine_mode must be either \"LOG_ONLY\" or \"ENFORCE\"."
  }
}

variable "enable_harness" {
  description = "Deploy an AgentCore Harness — the managed agent loop — alongside the code-defined Runtime. It reuses the same Gateway tools and Bedrock model, expressing the KYC assistant as configuration instead of code. On by default; set false to skip it."
  type        = bool
  default     = true
}

variable "container_cli" {
  description = "Container CLI used to build and push the two ARM64 images during apply. Either \"docker\" or \"finch\" — both expose a Docker-compatible CLI. scripts/deploy.sh sets this from the running engine (TF_VAR_container_cli); set it directly when running terraform by hand against Finch."
  type        = string
  default     = "docker"

  validation {
    condition     = contains(["docker", "finch"], var.container_cli)
    error_message = "container_cli must be either \"docker\" or \"finch\"."
  }
}

variable "enable_guardrail_binding_policy" {
  description = "Bind the Bedrock Guardrail to gateway traffic via an AgentCore Policy `when guardrails { … }` Cedar condition (see policy.tf). Left false because that Cedar extension is not yet live in the public CreatePolicy parser — verified 2026-08-11 against fresh policy engines in four Regions (us-east-1, eu-west-2, ap-northeast-1, ap-southeast-2), all of which reject it with \"unexpected token `guardrails`\" while a plain policy parses. It is a service-level preview rollout gap, not an account or Region entitlement. Setting this true today makes `terraform apply` fail on that policy; flip it on once AWS ships the parser extension. The policy statement itself is written and ready in policy.tf."
  type        = bool
  default     = false
}

variable "gateway_model_id" {
  description = "Model id as the Gateway's inference connector advertises it, used when inference_route = \"gateway\". The connector's catalog does not use Bedrock inference-profile ids, so this differs from model_id. List the options with: GET {gateway_url minus /mcp}/inference/v1/models"
  type        = string

  # DeepSeek rather than Claude, deliberately. The bedrock-mantle connector
  # invokes Anthropic models on-demand, and the newer Claude models
  # (sonnet-5, opus-*, haiku-4-5) are per-account entitlements gated behind
  # AWS Sales — an account without that entitlement gets a 403 "not available
  # for this account" that no console toggle or IAM change can clear. DeepSeek
  # is openly available through the same connector, so it demonstrates the
  # LLM-gateway path end to end on any account. Point this at a Claude model
  # once the account is entitled; the routing in lib/inference.py handles the
  # Anthropic vs OpenAI wire format automatically from the model id.
  default = "bedrock-mantle/deepseek.v3.1"
}

variable "inference_route" {
  description = "How the KYC agent invokes its LLM. \"direct\" calls Bedrock.InvokeModel from the runtime role (baseline). \"gateway\" calls the AgentCore Gateway /inference endpoint, which SigV4-forwards to Bedrock under the gateway role and applies the shared guardrail on every call. The demo defaults to \"gateway\" because that is the pattern this POC proves."
  type        = string
  default     = "gateway"
  validation {
    condition     = contains(["direct", "gateway"], var.inference_route)
    error_message = "inference_route must be either \"direct\" or \"gateway\"."
  }
}

variable "enable_registry_oauth_demo" {
  description = "Deploy the 'discover-then-invoke-via-OAuth' demo: a JWT-authorized twin runtime, a Cognito machine-to-machine client, an AgentCore OAuth2 credential provider + workload identity, a least-privilege consumer role, and an A2A registry record that advertises OAuth. It shows a consumer discovering an agent in the Registry and then calling it directly with an OAuth bearer token (no SigV4 on the agent call). Off by default; enable with -var enable_registry_oauth_demo=true. No effect on the console, gateway, runtime, or registry seeding when off."
  type        = bool
  default     = false
}
