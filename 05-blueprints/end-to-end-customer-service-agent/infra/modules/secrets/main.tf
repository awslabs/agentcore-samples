# KMS key for secrets encryption
resource "aws_kms_key" "secrets_key" {
  description = "KMS key for Secrets Manager encryption"
}

resource "aws_kms_alias" "secrets_key" {
  name          = "alias/secrets-manager"
  target_key_id = aws_kms_key.secrets_key.key_id
}

# Lambda function for secret rotation
data "archive_file" "rotation_lambda_zip" {
  type        = "zip"
  output_path = "rotation_lambda.zip"
  source_file = "${path.module}/rotation_lambda.py"
}

resource "aws_lambda_function" "rotation_lambda" {
  filename         = data.archive_file.rotation_lambda_zip.output_path
  function_name    = "secrets-rotation-lambda"
  role            = aws_iam_role.rotation_lambda_role.arn
  handler         = "rotation_lambda.lambda_handler"
  runtime         = "python3.9"
  timeout         = 30

  tracing_config {
    mode = "Active"
  }
}

resource "aws_iam_role" "rotation_lambda_role" {
  name = "secrets-rotation-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rotation_lambda_basic" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  role       = aws_iam_role.rotation_lambda_role.name
}

resource "aws_iam_role_policy_attachment" "rotation_lambda_xray" {
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
  role       = aws_iam_role.rotation_lambda_role.name
}

resource "aws_iam_role_policy" "rotation_lambda_secrets" {
  name = "secrets-rotation-policy"
  role = aws_iam_role.rotation_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecretVersionStage"
        ]
        Resource = [
          aws_secretsmanager_secret.cognito_client_secret.arn,
          aws_secretsmanager_secret.zendesk_credentials.arn,
          aws_secretsmanager_secret.langfuse_credentials.arn,
          aws_secretsmanager_secret.gateway_credentials.arn
        ]
      }
    ]
  })
}

resource "aws_lambda_permission" "allow_secrets_manager" {
  statement_id  = "AllowExecutionFromSecretsManager"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rotation_lambda.function_name
  principal     = "secretsmanager.amazonaws.com"
}

resource "aws_secretsmanager_secret" "cognito_client_secret" {
  name       = "cognito_client_secret"
  kms_key_id = aws_kms_key.secrets_key.key_id
}

resource "aws_secretsmanager_secret_version" "cognito_client_secret" {
  secret_id     = aws_secretsmanager_secret.cognito_client_secret.id
  secret_string = var.cognito_client_secret
}

resource "aws_secretsmanager_secret_rotation" "cognito_client_secret" {
  secret_id           = aws_secretsmanager_secret.cognito_client_secret.id
  rotation_lambda_arn = aws_lambda_function.rotation_lambda.arn
  
  rotation_rules {
    automatically_after_days = 30
  }
}

resource "aws_secretsmanager_secret" "zendesk_credentials" {
  name       = "zendesk_credentials"
  kms_key_id = aws_kms_key.secrets_key.key_id
}

resource "aws_secretsmanager_secret_version" "zendesk_credentials" {
  secret_id = aws_secretsmanager_secret.zendesk_credentials.id
  secret_string = jsonencode({
    zendesk_domain    = var.zendesk_domain
    zendesk_email     = var.zendesk_email
    zendesk_api_token = var.zendesk_api_token
  })
}

resource "aws_secretsmanager_secret_rotation" "zendesk_credentials" {
  secret_id           = aws_secretsmanager_secret.zendesk_credentials.id
  rotation_lambda_arn = aws_lambda_function.rotation_lambda.arn
  
  rotation_rules {
    automatically_after_days = 90
  }
}

resource "aws_secretsmanager_secret" "langfuse_credentials" {
  name       = "langfuse_credentials"
  kms_key_id = aws_kms_key.secrets_key.key_id
}

resource "aws_secretsmanager_secret_version" "langfuse_credentials" {
  secret_id = aws_secretsmanager_secret.langfuse_credentials.id
  secret_string = jsonencode({
    langfuse_host       = var.langfuse_host
    langfuse_public_key = var.langfuse_public_key
    langfuse_secret_key = var.langfuse_secret_key
  })
}

resource "aws_secretsmanager_secret_rotation" "langfuse_credentials" {
  secret_id           = aws_secretsmanager_secret.langfuse_credentials.id
  rotation_lambda_arn = aws_lambda_function.rotation_lambda.arn
  
  rotation_rules {
    automatically_after_days = 90
  }
}

resource "aws_secretsmanager_secret" "gateway_credentials" {
  name       = "gateway_credentials"
  kms_key_id = aws_kms_key.secrets_key.key_id
}

resource "aws_secretsmanager_secret_version" "gateway_credentials" {
  secret_id = aws_secretsmanager_secret.gateway_credentials.id
  secret_string = jsonencode({
    gateway_url = var.gateway_url
    api_key     = var.gateway_api_key
  })
}

resource "aws_secretsmanager_secret_rotation" "gateway_credentials" {
  secret_id           = aws_secretsmanager_secret.gateway_credentials.id
  rotation_lambda_arn = aws_lambda_function.rotation_lambda.arn
  
  rotation_rules {
    automatically_after_days = 90
  }
}

# Tavily secret removed — replaced by AgentCore Web Search Tool (managed connector)