variable "cognito_client_secret" {
  description = "Cognito client secret"
  type        = string
  sensitive   = true
}

variable "zendesk_domain" {
  description = "Zendesk domain"
  type        = string
}

variable "zendesk_email" {
  description = "Zendesk email"
  type        = string
}

variable "zendesk_api_token" {
  description = "Zendesk API token"
  type        = string
  sensitive   = true
}

variable "langfuse_host" {
  description = "Langfuse host"
  type        = string
}

variable "langfuse_public_key" {
  description = "Langfuse public key"
  type        = string
}

variable "langfuse_secret_key" {
  description = "Langfuse secret key"
  type        = string
  sensitive   = true
}

variable "gateway_url" {
  description = "Gateway URL"
  type        = string
}

variable "gateway_api_key" {
  description = "Gateway API key"
  type        = string
  sensitive   = true
}

variable "kms_key_id" {
  description = "KMS key ID for encrypting secrets"
  type        = string
  default     = null
}