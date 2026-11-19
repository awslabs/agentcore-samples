# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# AgentCore Memory
#
# Holds KYC assessment history keyed by corporate customer (actorId =
# customer_id), so any analyst re-assessing a customer sees what the bank
# concluded previously.
#
# Two long-term strategies run over the conversational events:
#   semantic — retrievable facts about the customer's risk posture
#   summary  — a rolling synopsis of the assessment history
# =============================================================================

data "aws_iam_policy_document" "memory_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "memory" {
  name               = "${var.stack_name}-memory-role"
  assume_role_policy = data.aws_iam_policy_document.memory_assume_role.json
  description        = "Execution role for AgentCore Memory extraction strategies"
}

# Long-term strategies invoke a Bedrock model to extract facts and summaries.
resource "aws_iam_role_policy_attachment" "memory_bedrock" {
  role       = aws_iam_role.memory.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy"
}

resource "aws_bedrockagentcore_memory" "kyc" {
  name                      = local.memory_name
  description               = "KYC assessment history per corporate customer"
  event_expiry_duration     = var.memory_event_expiry_days
  memory_execution_role_arn = aws_iam_role.memory.arn

  tags = {
    Name = local.memory_name
  }

  depends_on = [aws_iam_role_policy_attachment.memory_bedrock]
}

# Long-term strategies are separate resources, not inline blocks.
# SEMANTIC extracts retrievable facts; the agent queries this namespace on recall.
resource "aws_bedrockagentcore_memory_strategy" "assessment_facts" {
  memory_id           = aws_bedrockagentcore_memory.kyc.id
  name                = "kyc_assessment_facts"
  description         = "Extracts durable risk facts from each assessment"
  type                = "SEMANTIC"
  namespace_templates = ["/kyc/{actorId}/assessments"]
}

# SUMMARIZATION summarizes one session at a time, so the service requires
# {sessionId} in the namespace — unlike SEMANTIC, which aggregates per actor.
resource "aws_bedrockagentcore_memory_strategy" "assessment_summary" {
  memory_id           = aws_bedrockagentcore_memory.kyc.id
  name                = "kyc_assessment_summary"
  description         = "Per-assessment summary of each KYC review session"
  type                = "SUMMARIZATION"
  namespace_templates = ["/kyc/{actorId}/summary/{sessionId}"]

  # Strategies on one memory must be created serially — concurrent writes to the
  # same memory resource conflict.
  depends_on = [aws_bedrockagentcore_memory_strategy.assessment_facts]
}
