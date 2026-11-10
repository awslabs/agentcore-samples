# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # >= 6.58.0: fixes aws_bedrockagentcore_memory_strategy's
      # "too many results: wanted 1, got 2" error when a memory has two
      # strategies of different types (our SEMANTIC + SUMMARIZATION pair).
      # Older providers leave the second strategy orphaned on a fresh apply.
      version = ">= 6.58.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.11.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
}
