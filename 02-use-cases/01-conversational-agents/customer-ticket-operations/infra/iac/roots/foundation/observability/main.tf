# Account/Region-wide Bedrock model invocation logging.
# In reuse mode the existing destination is referenced only; do not take ownership of it.
locals {
  create_invocation_logging = var.existing_invocation_log_group == null
  log_group                 = "/aws/bedrock/${var.name_prefix}_invocations"
  invocation_log_group      = local.create_invocation_logging ? aws_cloudwatch_log_group.invocations[0].name : var.existing_invocation_log_group
}

moved {
  from = aws_cloudwatch_log_group.invocations
  to   = aws_cloudwatch_log_group.invocations[0]
}

resource "aws_cloudwatch_log_group" "invocations" {
  count             = local.create_invocation_logging ? 1 : 0
  name              = local.log_group
  retention_in_days = 3
}

moved {
  from = data.aws_iam_policy_document.bedrock_trust
  to   = data.aws_iam_policy_document.bedrock_trust[0]
}

data "aws_iam_policy_document" "bedrock_trust" {
  count = local.create_invocation_logging ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bedrock:${var.region}:${var.account_id}:*"]
    }
  }
}

moved {
  from = aws_iam_role.invocation_logging
  to   = aws_iam_role.invocation_logging[0]
}

resource "aws_iam_role" "invocation_logging" {
  count              = local.create_invocation_logging ? 1 : 0
  name               = "${var.name_prefix}_invocation_logging"
  description        = "Ticket operations demo; lets Bedrock write model invocation logs"
  assume_role_policy = data.aws_iam_policy_document.bedrock_trust[0].json
}

moved {
  from = data.aws_iam_policy_document.write_logs
  to   = data.aws_iam_policy_document.write_logs[0]
}

data "aws_iam_policy_document" "write_logs" {
  count = local.create_invocation_logging ? 1 : 0

  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.invocations[0].arn}:log-stream:aws/bedrock/modelinvocations"]
  }
}

moved {
  from = aws_iam_role_policy.write_logs
  to   = aws_iam_role_policy.write_logs[0]
}

resource "aws_iam_role_policy" "write_logs" {
  count  = local.create_invocation_logging ? 1 : 0
  name   = "write_invocation_logs"
  role   = aws_iam_role.invocation_logging[0].id
  policy = data.aws_iam_policy_document.write_logs[0].json
}

# A new role is not usable by Bedrock for a few seconds (IAM eventual consistency).
moved {
  from = time_sleep.iam_propagation
  to   = time_sleep.iam_propagation[0]
}

resource "time_sleep" "iam_propagation" {
  count           = local.create_invocation_logging ? 1 : 0
  depends_on      = [aws_iam_role_policy.write_logs]
  create_duration = "20s"
}

moved {
  from = aws_bedrock_model_invocation_logging_configuration.this
  to   = aws_bedrock_model_invocation_logging_configuration.this[0]
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  count      = local.create_invocation_logging ? 1 : 0
  depends_on = [time_sleep.iam_propagation]

  logging_config {
    text_data_delivery_enabled      = true
    image_data_delivery_enabled     = false
    embedding_data_delivery_enabled = false
    video_data_delivery_enabled     = false

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.invocations[0].name
      role_arn       = aws_iam_role.invocation_logging[0].arn
    }
  }
}

# ── CloudWatch Transaction Search: X-Ray spans land in CloudWatch Logs (aws/spans), 100% indexed ──
# AgentCore Runtime exports the agent's OpenTelemetry spans; GenAI Observability, the console's
# Trace view and AgentCore Evaluations all read them from here.
data "aws_iam_policy_document" "xray_to_logs" {
  statement {
    sid     = "TransactionSearchXRayAccess"
    actions = ["logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:aws/spans:*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/application-signals/data:*",
    ]
    principals {
      type        = "Service"
      identifiers = ["xray.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:xray:${var.region}:${var.account_id}:*"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "xray" {
  policy_name     = "${var.name_prefix}_transaction_search"
  policy_document = data.aws_iam_policy_document.xray_to_logs.json
}

resource "aws_xray_trace_segment_destination" "this" {
  destination = "CloudWatchLogs"
  depends_on  = [aws_cloudwatch_log_resource_policy.xray]
}

resource "aws_xray_indexing_rule" "default" {
  name = "Default"
  rule {
    probabilistic {
      desired_sampling_percentage = 100 # every demo span is searchable
    }
  }
  depends_on = [aws_xray_trace_segment_destination.this]
}
