terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # target_configuration.mcp.connector — the block this PoC depends on — was
      # added in provider 6.63.0 (2026-09-03). Anything older will fail at plan
      # time with "Blocks of type connector are not expected here".
      version = "~> 6.69"
    }
  }
}
