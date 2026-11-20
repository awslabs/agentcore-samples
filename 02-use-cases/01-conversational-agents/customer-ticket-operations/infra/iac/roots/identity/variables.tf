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
