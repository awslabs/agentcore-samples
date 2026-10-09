# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Cognito — authentication for the hosted demo console
#
# The console signs in with USER_PASSWORD_AUTH and sends the resulting Cognito
# ID token as a bearer token to the console API, which validates it against the
# pool's JWKS. Hosted UI / OAuth redirect flows are deliberately not used: the
# console renders its own branded login form, so there is no second domain to
# configure and no callback-URL round trip in the demo.
# =============================================================================

resource "aws_cognito_user_pool" "console" {
  name = "${var.stack_name}-console-users"

  # Demo console: operators are provisioned by Terraform, never self-registered.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # MFA is off because this is a demo stack holding only synthetic data. A real
  # KYC console would set this to "ON" with software_token_mfa_configuration.
  mfa_configuration = "OFF"

  tags = {
    Name = "${var.stack_name}-console-users"
  }
}

resource "aws_cognito_user_pool_client" "console" {
  name         = "${var.stack_name}-console-client"
  user_pool_id = aws_cognito_user_pool.console.id

  # Public SPA client: no secret, since a browser cannot keep one.
  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  id_token_validity      = 8
  access_token_validity  = 8
  refresh_token_validity = 30

  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
  supported_identity_providers  = ["COGNITO"]
}

# -----------------------------------------------------------------------------
# Identity pool — exchanges the ID token for temporary IAM credentials
#
# This account blocks Lambda Function URLs with authorization_type NONE (an
# unsigned request returns 403 before it reaches the function), so the browser
# must SigV4-sign its calls. An identity pool federates the user pool's ID token
# into short-lived credentials whose only permission is to invoke this one
# Function URL.
#
# The user pool still governs *who* may sign in; the identity pool only converts
# that proven identity into signing credentials.
# -----------------------------------------------------------------------------

resource "aws_cognito_identity_pool" "console" {
  identity_pool_name               = "${var.stack_name}-console-identities"
  allow_unauthenticated_identities = false

  cognito_identity_providers {
    client_id     = aws_cognito_user_pool_client.console.id
    provider_name = aws_cognito_user_pool.console.endpoint
    # Reject tokens the user pool did not issue for this client.
    server_side_token_check = true
  }
}

data "aws_iam_policy_document" "console_web_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = ["cognito-identity.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "cognito-identity.amazonaws.com:aud"
      values   = [aws_cognito_identity_pool.console.id]
    }

    # Only signed-in users — never the unauthenticated role.
    condition {
      test     = "ForAnyValue:StringLike"
      variable = "cognito-identity.amazonaws.com:amr"
      values   = ["authenticated"]
    }
  }
}

resource "aws_iam_role" "console_web" {
  name               = "${var.stack_name}-console-web-role"
  assume_role_policy = data.aws_iam_policy_document.console_web_assume_role.json
  description        = "Role assumed by signed-in console users to sign API calls"
}

data "aws_iam_policy_document" "console_web" {
  # Deliberately narrow: invoking the console API's Function URL is the only
  # thing a browser credential can do. Every AWS call beyond that is made by the
  # API's own execution role, after it validates the caller's ID token.
  # Both actions are required. lambda:InvokeFunctionUrl alone authorizes
  # reaching the URL, but the underlying invocation is still checked against
  # lambda:InvokeFunction — omit it and the request is rejected with a bare 403
  # while `aws iam simulate-principal-policy --action-names
  # lambda:InvokeFunctionUrl` reports "allowed", which reads like a signing bug.
  statement {
    sid    = "InvokeConsoleApi"
    effect = "Allow"
    actions = [
      "lambda:InvokeFunctionUrl",
      "lambda:InvokeFunction",
    ]
    resources = [
      aws_lambda_function.console_api.arn,
      "${aws_lambda_function.console_api.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "console_web" {
  name   = "${var.stack_name}-console-web-policy"
  role   = aws_iam_role.console_web.id
  policy = data.aws_iam_policy_document.console_web.json
}

resource "aws_cognito_identity_pool_roles_attachment" "console" {
  identity_pool_id = aws_cognito_identity_pool.console.id

  roles = {
    authenticated = aws_iam_role.console_web.arn
  }
}

# -----------------------------------------------------------------------------
# Demo operator
#
# Cognito's admin_create_user always lands the user in FORCE_CHANGE_PASSWORD,
# which would make the demo's first login a password-reset challenge. Setting a
# permanent password immediately afterwards clears that so the account is
# usable as-is.
# -----------------------------------------------------------------------------

resource "random_password" "console_user" {
  count = var.console_user_email == null ? 0 : 1

  length           = 20
  min_lower        = 2
  min_upper        = 2
  min_numeric      = 2
  min_special      = 2
  override_special = "!#$%*-_=+"
}

locals {
  # An explicitly supplied password wins; otherwise use the generated one.
  console_user_password = try(
    coalesce(var.console_user_password, random_password.console_user[0].result),
    null
  )
}

resource "aws_cognito_user" "console" {
  count = var.console_user_email == null ? 0 : 1

  user_pool_id = aws_cognito_user_pool.console.id
  username     = var.console_user_email

  attributes = {
    email          = var.console_user_email
    email_verified = true
  }

  password       = local.console_user_password
  message_action = "SUPPRESS"

  lifecycle {
    # The password is write-only in the API, so Terraform cannot read it back
    # and would otherwise propose a change on every plan.
    ignore_changes = [password]
  }
}
