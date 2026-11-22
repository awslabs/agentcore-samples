terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0" # 6.69.1 verified 2026-10-21
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.0" # Automated Reasoning resources are only in awscc
    }
  }
}

provider "aws" {
  region = var.region

  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Root        = "guardrails"
    }
  }
}

provider "awscc" {
  region = var.region
}
