variable "orchestrator_name" {
  description = "Name for the orchestrator agent runtime"
  type        = string
  default     = "OrchestratorAgent"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,47}$", var.orchestrator_name))
    error_message = "Agent name must start with a letter, max 48 characters, alphanumeric and underscores only."
  }
}

variable "specialist_name" {
  description = "Name for the specialist agent runtime"
  type        = string
  default     = "SpecialistAgent"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9_]{0,47}$", var.specialist_name))
    error_message = "Agent name must start with a letter, max 48 characters, alphanumeric and underscores only."
  }
}

variable "network_mode" {
  description = "Network mode for AgentCore resources"
  type        = string
  default     = "PUBLIC"

  validation {
    condition     = contains(["PUBLIC", "PRIVATE"], var.network_mode)
    error_message = "Network mode must be either PUBLIC or PRIVATE."
  }
}

variable "image_tag" {
  description = "Docker image tag"
  type        = string
  default     = "latest"

  validation {
    condition     = can(regex("^[a-zA-Z0-9._-]+$", var.image_tag))
    error_message = "Image tag must contain only alphanumeric characters, dots, underscores, and hyphens."
  }
}

variable "aws_region" {
  description = "AWS region for deployment (REQUIRED)"
  type        = string
  # No default - must be explicitly provided

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-\\d{1}$", var.aws_region))
    error_message = "Must be a valid AWS region (e.g., us-east-1, eu-west-1)"
  }
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "stack_name" {
  description = "Stack name for resource naming"
  type        = string
  default     = "agentcore-multi-agent"
}

variable "environment_variables" {
  description = "Environment variables for the agent runtime"
  type        = map(string)
  default     = {}
}

variable "ecr_repository_name" {
  description = "Base name of the ECR repositories"
  type        = string
  default     = "multi-agent"
}
