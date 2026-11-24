locals {
  outputs = {
    api_function_name = aws_lambda_function.api.function_name
    api_function_url  = aws_lambda_function_url.api.function_url
    tickets_table     = aws_dynamodb_table.tickets.name
    tickets_table_arn = aws_dynamodb_table.tickets.arn
    state_table       = aws_dynamodb_table.state.name
    state_table_arn   = aws_dynamodb_table.state.arn
    events_table      = aws_dynamodb_table.events.name
    events_table_arn  = aws_dynamodb_table.events.arn
  }
}

resource "aws_ssm_parameter" "app" {
  for_each = local.outputs
  name     = "${local.ssm}/app/${each.key}"
  type     = "String"
  value    = each.value
}

output "app" {
  value = local.outputs
}
