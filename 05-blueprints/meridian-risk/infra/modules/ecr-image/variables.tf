# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

variable "repository_name" {
  description = "ECR repository name."
  type        = string
}

variable "build_context" {
  description = "Absolute path to the Docker build context (the directory holding the Dockerfile)."
  type        = string
}

variable "source_files" {
  description = "Every file whose content should trigger a rebuild — the Dockerfile, dependency manifests, and sources. Hashed together into the image hash."
  type        = list(string)

  validation {
    condition     = length(var.source_files) > 0
    error_message = "source_files must not be empty, or the image would never rebuild on a code change."
  }
}

variable "region" {
  description = "AWS region hosting the repository."
  type        = string
}

variable "account_id" {
  description = "AWS account ID, used for the ECR registry hostname."
  type        = string
}

variable "retained_image_count" {
  description = "How many recent images the lifecycle policy keeps."
  type        = number
  default     = 5

  validation {
    condition     = var.retained_image_count >= 1
    error_message = "At least one image must be retained."
  }
}

variable "container_cli" {
  description = "Container CLI used to build, log in, and push the image. Docker and Finch are both Docker-compatible; the build detects and applies the buildx-only --provenance/--sbom flags only when the CLI supports them."
  type        = string
  default     = "docker"

  validation {
    condition     = contains(["docker", "finch"], var.container_cli)
    error_message = "container_cli must be either \"docker\" or \"finch\"."
  }
}
