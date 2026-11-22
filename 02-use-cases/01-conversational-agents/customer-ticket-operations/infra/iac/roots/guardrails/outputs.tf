resource "aws_ssm_parameter" "guardrail_id" {
  name  = "/${var.project}/${var.environment}/guardrails/prompt_attack_id"
  type  = "String"
  value = aws_bedrock_guardrail.prompt_attack.guardrail_id

  lifecycle {
    precondition {
      condition     = aws_bedrock_guardrail.prompt_attack.guardrail_arn == "arn:aws:bedrock:${var.region}:${var.account_id}:guardrail/${aws_bedrock_guardrail.prompt_attack.guardrail_id}"
      error_message = "Do not publish a Guardrail ID from a different account or region."
    }
  }
}

resource "aws_ssm_parameter" "ar_guardrail_id" {
  name  = "/${var.project}/${var.environment}/guardrails/automated_reasoning_id"
  type  = "String"
  value = awscc_bedrock_guardrail.automated_reasoning.guardrail_id
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.prompt_attack.guardrail_id
}

output "ar_guardrail_id" {
  value = awscc_bedrock_guardrail.automated_reasoning.guardrail_id
}

output "ar_policy_version_arn" {
  value = "${awscc_bedrock_automated_reasoning_policy.rule_change.policy_arn}:${awscc_bedrock_automated_reasoning_policy_version.rule_change.version}"
}
