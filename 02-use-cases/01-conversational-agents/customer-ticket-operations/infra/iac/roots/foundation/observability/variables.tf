variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "account_id" {
  description = "AWS account ID for this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for demo resource names; the sample code looks resources up by these names."
  type        = string
  default     = "ticketops_demo"
}

variable "existing_invocation_log_group" {
  description = "Optional existing Bedrock CloudWatch invocation-log group name. When set, do not manage the account-wide Bedrock logging configuration or its destination/role; only publish this name in the new environment's SSM pointer."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = var.existing_invocation_log_group == null ? true : (
      length(var.existing_invocation_log_group) <= 512 &&
      can(regex("^/aws/bedrock/[A-Za-z0-9_./-]+$", var.existing_invocation_log_group)) &&
      !strcontains(var.existing_invocation_log_group, "..") &&
      !endswith(var.existing_invocation_log_group, "/")
    )
    error_message = "existing_invocation_log_group must be null or a nonempty /aws/bedrock/ CloudWatch log group name (up to 512 characters, no dot-dot or trailing slash)."
  }
}
