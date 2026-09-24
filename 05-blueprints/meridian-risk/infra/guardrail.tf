# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Bedrock Guardrail — governance rail for the Gateway's inference target
#
# The intended design is one enforceable rail across every model the Gateway
# fronts, rather than each caller wiring guardrails per InvokeModel — the point
# of the LLM-gateway pattern in this POC. Binding it to gateway traffic is done
# through AgentCore Policy (a Cedar `when guardrails { … }` condition — see
# policy.tf), which is the only mechanism the inference target exposes. That
# binding is gated off until the Cedar extension ships in the public API, so
# today this guardrail is deployed and versioned as a reviewable artifact and
# enforced only on callers that invoke Bedrock directly — not yet on the
# /inference path. Flip var.enable_guardrail_binding_policy on to bind it.
#
# What we block, and why:
#   - Sensitive PII (SSN, credit-card, bank-account) — a KYC assistant handles
#     applicant records; the model should never echo raw account numbers back.
#   - Prompt-injection & jailbreak content-filters at HIGH — the specialists
#     ingest applicant-supplied text via the analyst notes field.
#   - Denied topic: unsolicited financial advice — the model is a risk
#     assessor, not a broker; refuse to recommend investments.
# =============================================================================

resource "aws_bedrock_guardrail" "kyc" {
  name                      = "${var.stack_name}-guardrail"
  description               = "PII redaction + injection filters + denied topics for KYC assessment traffic."
  blocked_input_messaging   = "Your request contains content that policy does not allow. Rephrase without personal identifiers or unrelated financial-advice questions."
  blocked_outputs_messaging = "The generated response was blocked by policy. Redacting and returning a safe alternative."

  content_policy_config {
    # Prompt-injection and jailbreak detection. HIGH on both input and output
    # because analyst notes and tool responses both reach the model.
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "MEDIUM"
      output_strength = "MEDIUM"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
  }

  sensitive_information_policy_config {
    # Anonymize on output rather than block outright: an assessment must be
    # able to *reference* an applicant's account without printing the number.
    pii_entities_config {
      type   = "US_SOCIAL_SECURITY_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "US_BANK_ACCOUNT_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "US_BANK_ROUTING_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
  }

  topic_policy_config {
    topics_config {
      name       = "unsolicited_financial_advice"
      type       = "DENY"
      definition = "Suggestions to buy, sell, or hold specific securities, or personalized investment recommendations for individuals."
      examples = [
        "Should I invest in Acme stock?",
        "Recommend a portfolio for my client's retirement.",
        "Is now a good time to buy Bitcoin?",
      ]
    }
  }
}

# A published version is required — the inference target references
# guardrailVersion, not the DRAFT working copy.
resource "aws_bedrock_guardrail_version" "kyc" {
  guardrail_arn = aws_bedrock_guardrail.kyc.guardrail_arn
  description   = "Initial version — POC baseline for KYC guardrail."
}
