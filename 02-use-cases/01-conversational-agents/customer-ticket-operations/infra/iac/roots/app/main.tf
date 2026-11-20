# Application state and the presenter API. Tickets live in DynamoDB (the agent's tools read and
# change them through the Gateway); the API is a Lambda function URL that only CloudFront can call.

locals {
  ssm = "/${var.project}/${var.environment}"
  seed = {
    "A-41" = { customer_id = "customer-a", subject = "Package arrived late", body = "Please explain the delay." }
    "A-42" = { customer_id = "customer-a", subject = "Correct billing address", body = "Please update the postal code on my next invoice." }
    "B-77" = { customer_id = "customer-b", subject = "Refund status", body = "When will my refund arrive?" }
  }
}

data "aws_ssm_parameter" "user_pool_arn" {
  name = "${local.ssm}/identity/user_pool_arn"
}

data "aws_ssm_parameter" "issuer" {
  name = "${local.ssm}/identity/issuer"
}

data "aws_ssm_parameter" "invocation_log_group" {
  name = "${local.ssm}/observability/invocation_log_group"
}

# ── Tables ─────────────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "tickets" {
  name         = "${var.name_prefix}_tickets"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "customer_id"
  range_key    = "ticket_id"

  attribute {
    name = "customer_id"
    type = "S"
  }
  attribute {
    name = "ticket_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table" "state" {
  name         = "${var.name_prefix}_state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "customer_id"

  attribute {
    name = "customer_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# Live run events for the console's architecture panel (API, agent hooks and TicketTools write;
# the API reads). Short-lived: items expire after a day.
resource "aws_dynamodb_table" "events" {
  name         = "${var.name_prefix}_events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "sk"

  attribute {
    name = "run_id"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table_item" "seed" {
  for_each   = local.seed
  table_name = aws_dynamodb_table.tickets.name
  hash_key   = aws_dynamodb_table.tickets.hash_key
  range_key  = aws_dynamodb_table.tickets.range_key
  item = jsonencode({
    customer_id  = { S = each.value.customer_id }
    ticket_id    = { S = each.key }
    subject      = { S = each.value.subject }
    body         = { S = each.value.body }
    status       = { S = "OPEN" }
    submitted_by = { S = "customer" }
    untrusted    = { BOOL = true }
    origin       = { S = "seed" }
  })

  lifecycle {
    ignore_changes = [item] # the demo changes ticket state; reset is an API action, not a deploy
  }
}

# ── Presenter API ──────────────────────────────────────────────────────────
module "build" {
  source      = "../../modules/build_artifacts"
  project     = var.project
  environment = var.environment
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}_api"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "api_logs" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "api" {
  statement {
    sid = "Tables"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
    ]
    resources = [aws_dynamodb_table.tickets.arn, aws_dynamodb_table.state.arn]
  }
  statement {
    sid       = "Events"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.events.arn]
  }
  statement {
    # written by later roots (web, agent); read at request time to avoid a deploy-order cycle
    sid     = "LaterRootOutputs"
    actions = ["ssm:GetParameter"]
    resources = [for p in ["edge/client_id", "agent/runtime_arn", "agent/model_id", "agent/policy_engines", "agent/gateways", "agent/gateway_urls", "agent/gateway_logs"] :
    "arn:aws:ssm:${var.region}:${var.account_id}:parameter${local.ssm}/${p}"]
  }
  statement {
    sid       = "StartEvalSuite" # async invoke of the eval suite runner, deployed by the evals root
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}_evals"]
  }
  statement {
    sid     = "TraceQueries" # the console's Trace view: Logs Insights over AWS's own records
    actions = ["logs:StartQuery"]
    resources = [
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:${data.aws_ssm_parameter.invocation_log_group.value}:*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/vendedlogs/bedrock-agentcore/gateway/*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:aws/spans:*",
      "arn:aws:logs:${var.region}:${var.account_id}:log-group:/aws/bedrock-agentcore/runtimes/*",
    ]
  }
  statement {
    sid       = "TraceResults"
    actions   = ["logs:GetQueryResults", "logs:DescribeLogGroups"]
    resources = ["*"] # neither supports resource-level permissions
  }
  statement {
    sid       = "ReadBuildPolicies" # the console's Controls view shows the live Cedar text
    actions   = ["bedrock-agentcore:ListPolicies", "bedrock-agentcore:GetPolicy"]
    resources = ["arn:aws:bedrock-agentcore:${var.region}:${var.account_id}:policy-engine/*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.name_prefix}_api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "app.lambda_handler"
  timeout          = 60 # an agent run takes 10-40 s; CloudFront waits up to 60 s for this origin
  memory_size      = 256
  s3_bucket        = module.build.bucket
  s3_key           = module.build.lambda["api"].key
  source_code_hash = module.build.lambda["api"].hash

  environment {
    variables = {
      TICKETS_TABLE        = aws_dynamodb_table.tickets.name
      STATE_TABLE          = aws_dynamodb_table.state.name
      EVENTS_TABLE         = aws_dynamodb_table.events.name
      INVOCATION_LOG_GROUP = data.aws_ssm_parameter.invocation_log_group.value
      ISSUER               = data.aws_ssm_parameter.issuer.value
      CLIENT_ID_PARAM      = "${local.ssm}/edge/client_id"
      AGENT_PARAM_PREFIX   = "${local.ssm}/agent"
      EVALS_FUNCTION       = "${var.name_prefix}_evals" # the eval suite runner (evals root)
    }
  }
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "AWS_IAM" # only principals with lambda:InvokeFunctionUrl: CloudFront via OAC
}

# ── Run metrics (EMF from the API, namespace TicketOpsDemo) and alarms ─────────
locals {
  alarms = {
    forbidden_effect = { metric = "ForbiddenEffects", what = "a run changed a ticket nobody asked to change" }
    policy_denials   = { metric = "PolicyDenials", what = "AgentCore Policy denied a tool call" }
    guardrail_blocks = { metric = "GuardrailBlocks", what = "Bedrock Guardrails blocked ticket text" }
    stop_refusals    = { metric = "StopRefusals", what = "a run was refused because the agent is stopped" }
  }
}

resource "aws_cloudwatch_metric_alarm" "run" {
  for_each            = local.alarms
  alarm_name          = "${var.name_prefix}_${each.key}"
  alarm_description   = "Ticket operations demo: ${each.value.what}"
  namespace           = "TicketOpsDemo"
  metric_name         = each.value.metric
  dimensions          = { Build = "V5" }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "forbidden_effect_any_build" {
  alarm_name          = "${var.name_prefix}_forbidden_effect_any_build"
  alarm_description   = "Ticket operations demo: any build changed a ticket through the agent (V0-V2 do, by design)"
  namespace           = "TicketOpsDemo"
  metric_name         = "ForbiddenEffects"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

# V4+: the circuit breaker tripped (a policy denial or the turn limit) and the agent is read-only
# until an operator resets it. Someone should look: the breaker never closes on its own.
resource "aws_cloudwatch_metric_alarm" "breaker_tripped" {
  alarm_name          = "${var.name_prefix}_breaker_tripped"
  alarm_description   = "Ticket operations demo: the agent's circuit breaker tripped; it stays read-only until an operator resets it"
  namespace           = "TicketOpsDemo"
  metric_name         = "BreakerTrips"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
