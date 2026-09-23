# =============================================================================
# Pre Token Generation Lambda
# =============================================================================

# --- IAM Role ---
resource "aws_iam_role" "pre_token_lambda" {
  name = "PreTokenLambdaRole-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "pre_token_lambda_basic" {
  role       = aws_iam_role.pre_token_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- Lambda Package ---
data "archive_file" "pre_token_generation" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_src/pre_token_generation"
  output_path = "${path.module}/.build/pre_token_generation.zip"
}

# --- Lambda Function ---
resource "aws_lambda_function" "pre_token_generation" {
  function_name    = "pre-token-generation-${local.suffix}"
  description      = "Pre Token Generation Lambda for Cognito User Pool"
  runtime          = "python3.13"
  handler          = "lambda_function.lambda_handler"
  role             = aws_iam_role.pre_token_lambda.arn
  filename         = data.archive_file.pre_token_generation.output_path
  source_code_hash = data.archive_file.pre_token_generation.output_base64sha256
}

# --- Cognito Permission to Invoke ---
resource "aws_lambda_permission" "cognito_pre_token" {
  statement_id  = "cognito-trigger-permission"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token_generation.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.this.arn
}

# =============================================================================
# Gateway Interceptor Lambda
# =============================================================================

# --- IAM Role ---
resource "aws_iam_role" "interceptor_lambda" {
  name = "InterceptorLambdaRole-${local.suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "interceptor_lambda_basic" {
  role       = aws_iam_role.interceptor_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- Lambda Package ---
data "archive_file" "gateway_interceptor" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_src/gateway_interceptor"
  output_path = "${path.module}/.build/gateway_interceptor.zip"
}

# --- Lambda Function ---
resource "aws_lambda_function" "gateway_interceptor" {
  function_name    = "gateway-interceptor-${local.suffix}"
  description      = "Gateway Interceptor for AgentCore Gateway"
  runtime          = "python3.13"
  handler          = "lambda_function.lambda_handler"
  role             = aws_iam_role.interceptor_lambda.arn
  filename         = data.archive_file.gateway_interceptor.output_path
  source_code_hash = data.archive_file.gateway_interceptor.output_base64sha256

  environment {
    variables = {
      DOWNSTREAM_CLIENT_ID     = aws_cognito_user_pool_client.downstream.id
      DOWNSTREAM_CLIENT_SECRET = aws_cognito_user_pool_client.downstream.client_secret
      COGNITO_DOMAIN           = "${local.cognito_domain}.auth.${local.region}.amazoncognito.com"
      RESOURCE_SERVER_ID       = local.resource_server_id
    }
  }

  depends_on = [aws_cognito_user_pool_domain.this]
}
