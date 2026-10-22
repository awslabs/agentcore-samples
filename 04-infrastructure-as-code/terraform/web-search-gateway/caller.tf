# ---------------------------------------------------------------------------
# Inbound auth: who is allowed to call the Gateway
#
# With AWS_IAM inbound auth, authorization is expressed entirely as an IAM
# policy on the *calling* identity. This file creates that side of the boundary.
# It is separate from the Gateway service role in main.tf, which only handles
# outbound auth to the Web Search backend.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "demo_caller_assume" {
  statement {
    sid     = "AllowAccountPrincipalsToAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }
}

resource "aws_iam_role" "demo_caller" {
  name               = "${var.name_prefix}-caller-role"
  description        = "Least-privilege demo caller: may invoke the Web Search Gateway and nothing else"
  assume_role_policy = data.aws_iam_policy_document.demo_caller_assume.json
}

data "aws_iam_policy_document" "caller_invoke" {
  statement {
    sid       = "InvokeGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.web_search.gateway_arn]
  }
}

resource "aws_iam_role_policy" "demo_caller_invoke" {
  name   = "${var.name_prefix}-invoke-gateway"
  role   = aws_iam_role.demo_caller.id
  policy = data.aws_iam_policy_document.caller_invoke.json
}

# ---------------------------------------------------------------------------
# Optional: grant an existing identity the same InvokeGateway permission, so you
# can call the Gateway with credentials you already hold instead of assuming the
# demo caller role above.
# ---------------------------------------------------------------------------

locals {
  existing_caller_is_role = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", var.existing_caller_principal_arn))
  existing_caller_name    = var.existing_caller_principal_arn == "" ? "" : element(split("/", var.existing_caller_principal_arn), length(split("/", var.existing_caller_principal_arn)) - 1)
}

resource "aws_iam_role_policy" "existing_caller" {
  count = var.existing_caller_principal_arn != "" && local.existing_caller_is_role ? 1 : 0

  name   = "${var.name_prefix}-invoke-gateway"
  role   = local.existing_caller_name
  policy = data.aws_iam_policy_document.caller_invoke.json
}

resource "aws_iam_user_policy" "existing_caller" {
  count = var.existing_caller_principal_arn != "" && !local.existing_caller_is_role ? 1 : 0

  name   = "${var.name_prefix}-invoke-gateway"
  user   = local.existing_caller_name
  policy = data.aws_iam_policy_document.caller_invoke.json
}
