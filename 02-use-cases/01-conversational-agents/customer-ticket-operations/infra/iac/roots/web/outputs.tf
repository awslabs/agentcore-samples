resource "aws_ssm_parameter" "url" {
  name  = "${local.ssm}/web/url"
  type  = "String"
  value = "https://${local.host}"
}

output "url" {
  value = "https://${local.host}"
}

output "web_client_id" {
  value = aws_cognito_user_pool_client.web.id
}
