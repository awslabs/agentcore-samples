output "cognito_client_secret_arn" {
  description = "ARN of the Cognito client secret"
  value       = aws_secretsmanager_secret.cognito_client_secret.arn
}

output "zendesk_credentials_arn" {
  description = "ARN of the Zendesk credentials secret"
  value       = aws_secretsmanager_secret.zendesk_credentials.arn
}

output "langfuse_credentials_arn" {
  description = "ARN of the Langfuse credentials secret"
  value       = aws_secretsmanager_secret.langfuse_credentials.arn
}

output "gateway_credentials_arn" {
  description = "ARN of the gateway credentials secret"
  value       = aws_secretsmanager_secret.gateway_credentials.arn
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for secrets encryption"
  value       = aws_kms_key.secrets_key.arn
}