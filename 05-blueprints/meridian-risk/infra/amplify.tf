# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# =============================================================================
# Amplify Hosting — the demo console
#
# A manual-deployment Amplify app (no Git connection): scripts/deploy_frontend.py
# builds the SPA, injects runtime config, zips it, and calls StartDeployment.
# This keeps the repo host-agnostic and avoids granting Amplify a GitLab token.
# =============================================================================

resource "aws_amplify_app" "console" {
  name        = "${var.stack_name}-console"
  description = "AgentCore FSI demo console — KYC onboarding risk assessment"
  platform    = "WEB"

  # SPA fallback: every non-asset path must serve index.html so client-side
  # routing and deep links work instead of returning Amplify's 404.
  custom_rule {
    source = "/<*>"
    target = "/index.html"
    status = "404-200"
  }

  # Baseline security headers. The console holds a Cognito ID token in memory,
  # so clickjacking and MIME-sniffing protections are worth having even in a demo.
  #
  # This field is asymmetric: CreateApp accepts ONLY the documented YAML
  # `customHeaders:` mapping and rejects a JSON array with
  # "Invalid spec, yaml parsing error ... top level must be key value pairs",
  # but GetApp reads the value back as a JSON array. So the two forms are not
  # interchangeable — writing the JSON array to avoid read-back drift makes the
  # app impossible to create at all. Write YAML, then ignore the field on
  # subsequent plans (below) to absorb the round-trip difference.
  custom_headers = <<-YAML
    customHeaders:
      - pattern: "**"
        headers:
          - key: X-Frame-Options
            value: DENY
          - key: X-Content-Type-Options
            value: nosniff
          - key: Referrer-Policy
            value: strict-origin-when-cross-origin
  YAML

  tags = {
    Name = "${var.stack_name}-console"
  }

  lifecycle {
    # The service normalizes custom_headers from the YAML we send into a JSON
    # array, so an un-ignored field shows a diff on every plan forever. Changing
    # the headers therefore needs this ignore removed for one apply.
    ignore_changes = [custom_headers]
  }
}

resource "aws_amplify_branch" "console" {
  app_id      = aws_amplify_app.console.id
  branch_name = "main"
  stage       = "PRODUCTION"
  description = "Demo console"

  # Deployments are pushed by the deploy script, not built by Amplify.
  enable_auto_build = false
}

# -----------------------------------------------------------------------------
# Frontend build and deploy
#
# Re-runs when the frontend sources change or when any injected config value
# changes (the API URL, the Cognito pool, or the region).
# -----------------------------------------------------------------------------

resource "terraform_data" "frontend_hash" {
  input = sha256(join("", concat(
    [for f in fileset("${local.repo_root}/frontend/src", "**") :
    filesha256("${local.repo_root}/frontend/src/${f}")],
    [
      filesha256("${local.repo_root}/frontend/index.html"),
      filesha256("${local.repo_root}/frontend/package.json"),
      filesha256("${local.repo_root}/frontend/vite.config.ts"),
    ],
  )))
}

resource "null_resource" "frontend_deploy" {
  triggers = {
    frontend_hash = terraform_data.frontend_hash.output
    api_url       = aws_lambda_function_url.console_api.function_url
    user_pool     = aws_cognito_user_pool_client.console.id
    identity_pool = aws_cognito_identity_pool.console.id
    app_id        = aws_amplify_app.console.id
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      "${local.python}" "${local.repo_root}/scripts/deploy_frontend.py" \
        --app-id "${aws_amplify_app.console.id}" \
        --branch "${aws_amplify_branch.console.branch_name}" \
        --api-url "${aws_lambda_function_url.console_api.function_url}" \
        --user-pool-id "${aws_cognito_user_pool.console.id}" \
        --client-id "${aws_cognito_user_pool_client.console.id}" \
        --identity-pool-id "${aws_cognito_identity_pool.console.id}" \
        --region "${local.region}"
    EOT
  }

  depends_on = [
    aws_amplify_branch.console,
    aws_lambda_function_url.console_api,
  ]
}
