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

variable "model_id" {
  description = "Bedrock model (inference profile) the agent calls; the demo needs one that follows ticket-text instructions."
  type        = string
  default     = "us.meta.llama4-maverick-17b-instruct-v1:0"
}

variable "model_regions" {
  description = "Regions the cross-region inference profile can route to."
  type        = list(string)
  default     = ["us-east-1", "us-east-2", "us-west-2"]
}
