data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "random_id" "suffix" {
  byte_length = 4
}

locals {
  account_id         = data.aws_caller_identity.current.account_id
  region             = data.aws_region.current.name
  suffix             = random_id.suffix.hex
  resource_server_id = "${var.name_prefix}-api-${local.suffix}"
  cognito_domain     = "${var.name_prefix}-${local.suffix}"
}
