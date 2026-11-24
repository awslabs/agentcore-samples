# Who the callers are: one Cognito user pool, three personas and a presenter, tenant + role stamped into tokens.
# Nothing here trusts the request body; every downstream check reads these verified claims.

locals {
  personas = {
    alice = { customer_id = "customer-a", role = "operator" } # runs the agent
    carol = { customer_id = "customer-a", role = "customer" } # submits tickets through the Ticket Portal
    bob   = { customer_id = "customer-b", role = "operator" } # tenant-isolation check
    # signs in once, then switches between the personas above (the sign-in edge function mints their
    # real Cognito tokens server-side); it has no tenant and cannot use the console or portal itself
    presenter = { customer_id = "none", role = "presenter" }
  }
  # the personas a presenter may act as
  actable = { for k, v in local.personas : k => v if v.role != "presenter" }
  ssm     = "/${var.project}/${var.environment}/identity"
}

# ── Pre-token Lambda (V2: customizes access tokens; needs the Essentials tier) ──
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

resource "aws_iam_role" "pre_token" {
  name               = "${var.name_prefix}_pre_token"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "pre_token_logs" {
  role       = aws_iam_role.pre_token.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "pre_token" {
  function_name    = "${var.name_prefix}_pre_token"
  role             = aws_iam_role.pre_token.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  timeout          = 5
  s3_bucket        = module.build.bucket
  s3_key           = module.build.lambda["pre_token"].key
  source_code_hash = module.build.lambda["pre_token"].hash
}

resource "aws_lambda_permission" "cognito" {
  statement_id  = "cognito-pre-token"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.this.arn
}

# ── User pool ──────────────────────────────────────────────────────────────
resource "aws_cognito_user_pool" "this" {
  name                = var.name_prefix
  user_pool_tier      = "ESSENTIALS"
  deletion_protection = "INACTIVE" # throwaway demo account

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 16
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 1
  }

  schema {
    name                     = "customer_id"
    attribute_data_type      = "String"
    mutable                  = true
    developer_only_attribute = false
    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  schema {
    name                     = "role"
    attribute_data_type      = "String"
    mutable                  = true
    developer_only_attribute = false
    string_attribute_constraints {
      min_length = 1
      max_length = 32
    }
  }

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.pre_token.arn
      lambda_version = "V2_0"
    }
  }

  lifecycle {
    ignore_changes = [schema] # Cognito schema attributes cannot be changed after creation
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain                = "ticketops-${var.account_id}"
  user_pool_id          = aws_cognito_user_pool.this.id
  managed_login_version = 2 # managed login (branding editor styles)
}

# ── Personas: permanent random passwords, kept only in Secrets Manager ──────
resource "random_password" "persona" {
  for_each         = local.personas
  length           = 24
  special          = true
  override_special = "!#%*-_=+"
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
}

resource "aws_cognito_user" "persona" {
  for_each     = local.personas
  user_pool_id = aws_cognito_user_pool.this.id
  username     = each.key
  password     = random_password.persona[each.key].result
  attributes = {
    "customer_id" = each.value.customer_id
    "role"        = each.value.role
  }
  message_action = "SUPPRESS"
}

resource "aws_secretsmanager_secret" "persona" {
  for_each                = local.personas
  name                    = "${var.project}/${var.environment}/personas/${each.key}"
  description             = "Ticket operations demo persona ${each.key} (${each.value.role}, ${each.value.customer_id})"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "persona" {
  for_each  = local.personas
  secret_id = aws_secretsmanager_secret.persona[each.key].id
  secret_string = jsonencode({
    username    = each.key
    password    = random_password.persona[each.key].result
    role        = each.value.role
    customer_id = each.value.customer_id
  })
}
