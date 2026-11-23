# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore Policy — enforceable authorization on gateway traffic
#
# This is the enforcement point the LLM-gateway pattern needs: the Policy
# Engine evaluates every request the Gateway receives, for tools and for
# inference alike, and it does so server-side. An agent cannot opt out of it
# the way it can ignore an in-process tool filter.
#
# ---------------------------------------------------------------------------
# Binding the Bedrock Guardrail — the one governed step still awaiting preview
#
# For gateway traffic, an AgentCore Policy is the ONLY way to bind a Bedrock
# Guardrail: the inference target's configuration is a tagged union that takes
# only `connector` or `provider` (no guardrail field), so the guardrail rides
# in through a Cedar `when guardrails { BedrockGuardrails::… }` condition on
# this engine. The guardrail_binding policy below is that statement, written
# and ready.
#
# It is gated off by default (var.enable_guardrail_binding_policy = false)
# because the `when guardrails` Cedar extension is not yet live in the public
# CreatePolicy parser. Verified 2026-08-11 against fresh policy engines in four
# Regions — us-east-1, eu-west-2, ap-northeast-1, ap-southeast-2 — every one
# rejecting it at the lexer with "unexpected token `guardrails`", while a plain
# Cedar statement on the same engine parses through to semantic validation.
# The AI authoring path (start-policy-generation, what `agentcore add policy`
# drives) likewise returns "cannot be expressed in Dogwood". So this is a
# service-level preview rollout gap — not an account or Region entitlement, and
# not fixable by validation_mode, since the token is rejected before findings
# run. The docs mark these Regions available; the API surface has not caught up.
#
# Until then the Bedrock Guardrail in guardrail.tf stays a standalone, versioned
# artifact — reviewable, and enforceable on callers that invoke Bedrock directly
# — and enforcement on gateway traffic is Cedar authorization rather than ML
# content scoring. Flip the flag on once AWS ships the extension: the engine,
# its IAM (bedrock:InvokeGuardrailChecks), and the gateway attachment are all
# already in place, so binding is a one-variable change.
#
# ---------------------------------------------------------------------------
# Constraints the service enforces on these statements
#
#   * A wildcard resource is rejected outright: "a wildcard resource was
#     detected. To avoid unexpected behavior changes, please constrain the
#     resource either to a specific AgentCore::Gateway resource or to the
#     AgentCore::Gateway resource type." Every statement is therefore scoped
#     to our gateway ARN.
#   * ENFORCE mode is default-deny, so the baseline permit is mandatory rather
#     than optional. Cedar's `forbid` always beats `permit`, which is what
#     makes a broad baseline safe next to the targeted denials.
# =============================================================================

locals {
  # Cedar identifies the gateway by ARN. Held in a local because every
  # statement needs it and the quoting is easy to get subtly wrong.
  gateway_cedar_arn = aws_bedrockagentcore_gateway.kyc.gateway_arn

  # Gateway action names are "<targetName>___<toolName>" — the same doubly
  # underscored form the MCP tool list uses, which is why the orchestrator
  # splits on "___" when displaying them.
  tool_action_prefix = "kyc-tools___"
}

resource "aws_bedrockagentcore_policy_engine" "kyc" {
  name        = "${local.name_underscore}_policy_engine"
  description = "Evaluates Cedar authorization policies on KYC gateway traffic."
}

# -----------------------------------------------------------------------------
# Baseline permit
#
# Required for ENFORCE to be survivable: without it the engine denies every
# tool call and every inference request. Scoped to this gateway so the engine
# cannot authorize traffic on some other gateway attached to it later.
# -----------------------------------------------------------------------------

resource "aws_bedrockagentcore_policy" "baseline_permit" {
  name             = "${local.name_underscore}_baseline_permit"
  policy_engine_id = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
  description      = "Allow normal gateway traffic; forbid policies carve out the exceptions."

  definition {
    cedar {
      statement = <<-CEDAR
        permit (
          principal,
          action,
          resource == AgentCore::Gateway::"${local.gateway_cedar_arn}"
        );
      CEDAR
    }
  }

  # Findings on a statement this broad are expected and advisory. Failing the
  # apply on them would make the baseline permit — the thing keeping ENFORCE
  # survivable — the most fragile resource in the stack.
  validation_mode = "IGNORE_ALL_FINDINGS"
}

# -----------------------------------------------------------------------------
# Enforce the customer allowlist at the gateway
#
# Every KYC tool takes a customer_id, and this deployment holds exactly three
# synthetic customers. A request for anything else is either a bug or an
# attempt to probe for data we do not have — and in a real deployment, the
# equivalent policy is how you stop an agent from pulling records outside the
# book of business it was scoped to.
#
# Why this rule and not per-agent tool scoping: Cedar can express the latter,
# but it needs a caller identity in the evaluation context to key on, and the
# only fields guaranteed present are those derived from the tool input schema
# — which the service generates from our own tool_spec.json. `customer_id` is
# therefore verifiable; an `assessment_role` we assume the gateway injects is
# not. A policy that references an absent context field evaluates in a way
# that can silently deny everything, which for `sanctions_screen` would break
# CUST003's escalation while looking like a tool failure. Enforce on what the
# schema guarantees.
#
# The per-specialist tool scoping remains where it belongs: the Skill objects
# grant tools, and the Assessment tab reports granted-vs-withheld as observed
# fact. That is a cooperative control; this policy is an enforceable one. The
# two are complementary rather than substitutes.
# -----------------------------------------------------------------------------

# This policy names a specific gateway action (kyc-tools___get_customer_profile),
# which the policy engine validates against the gateway target's synced tool
# schema. On a fresh deploy the target reaches READY a beat before its tools
# propagate into the engine's action namespace, so a policy created immediately
# fails with CREATE_FAILED: "unrecognized action". Verified by isolation — the
# identical statement created moments later, once the target has settled, reaches
# ACTIVE. So gate this policy on the target plus a short settle for the sync.
# (baseline_permit above uses an unqualified `action` and needs no such wait.)
resource "time_sleep" "gateway_tool_schema_sync" {
  depends_on      = [aws_bedrockagentcore_gateway_target.kyc_tools]
  create_duration = "30s"
}

resource "aws_bedrockagentcore_policy" "forbid_unknown_customers" {
  name             = "${local.name_underscore}_forbid_unknown_customers"
  policy_engine_id = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
  description      = "KYC tools may only be called for customers this deployment holds."

  definition {
    cedar {
      statement = <<-CEDAR
        forbid (
          principal,
          action == AgentCore::Action::"${local.tool_action_prefix}get_customer_profile",
          resource == AgentCore::Gateway::"${local.gateway_cedar_arn}"
        )
        unless {
          context.input.customer_id == "CUST001" ||
          context.input.customer_id == "CUST002" ||
          context.input.customer_id == "CUST003"
        };
      CEDAR
    }
  }

  validation_mode = "IGNORE_ALL_FINDINGS"

  # The action reference above is only valid once the gateway target's tools have
  # synced into the policy engine — see time_sleep.gateway_tool_schema_sync.
  depends_on = [time_sleep.gateway_tool_schema_sync]
}

# -----------------------------------------------------------------------------
# Bind the Bedrock Guardrail to the inference target (gated — see the header)
#
# This forbids a model call on the /inference target when the prompt trips the
# Bedrock prompt-attack safeguard. `BedrockGuardrails::PromptAttack` is a
# built-in that names its categories and threshold INLINE and calls
# bedrock:InvokeGuardrailChecks under the gateway role — it does not reference
# the guardrail_arn from guardrail.tf, so this is complementary to that
# standalone artifact rather than a pointer to it. `when guardrails { … }`
# replaces the standard `when { … }` block and cannot be mixed with it.
#
# count-gated rather than always-on: the `when guardrails` extension is not yet
# accepted by the public CreatePolicy parser (see the header), so creating this
# unconditionally would fail every apply. Flip var.enable_guardrail_binding_policy
# to true once AWS ships it; nothing else here changes.
resource "aws_bedrockagentcore_policy" "guardrail_binding" {
  count = var.enable_guardrail_binding_policy ? 1 : 0

  name             = "${local.name_underscore}_guardrail_prompt_attack"
  policy_engine_id = aws_bedrockagentcore_policy_engine.kyc.policy_engine_id
  description      = "Bind the Bedrock prompt-attack guardrail to the /inference target."

  definition {
    cedar {
      statement = <<-CEDAR
        forbid (
          principal,
          action == AgentCore::Action::"bedrock-mantle___POST:/inference",
          resource == AgentCore::Gateway::"${local.gateway_cedar_arn}"
        )
        when guardrails {
          BedrockGuardrails::PromptAttack(
            ["PROMPT_INJECTION"],
            [context.input.prompt]
          )["PROMPT_INJECTION"].confidenceScore.greaterThan(decimal("0.4"))
        };
      CEDAR
    }
  }

  validation_mode = "IGNORE_ALL_FINDINGS"

  # Same tool-schema settle the customer allowlist waits on: the inference
  # target action must be in the engine's namespace before the policy validates.
  depends_on = [time_sleep.gateway_tool_schema_sync]
}
