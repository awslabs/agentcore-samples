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

variable "judge_model_id" {
  description = "Bedrock model (inference profile) for the LLM-as-a-judge evaluator."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "judge_regions" {
  description = "Regions the judge's cross-region inference profile can route to."
  type        = list(string)
  default     = ["us-east-1", "us-east-2", "us-west-2"]
}
