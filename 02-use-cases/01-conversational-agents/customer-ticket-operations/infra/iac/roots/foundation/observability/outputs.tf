resource "aws_ssm_parameter" "invocation_log_group" {
  name  = "/${var.project}/${var.environment}/observability/invocation_log_group"
  type  = "String"
  value = local.invocation_log_group
}

output "invocation_log_group" {
  value = local.invocation_log_group
}
