variable "region" {
  description = "AWS Region to deploy into. Must be a Region where the AgentCore Web Search Tool connector is available."
  type        = string
  default     = "eu-west-1"

  validation {
    # Source: AgentCore devguide, "Web Search Tool" > Availability.
    condition = contains([
      "us-east-1",
      "us-east-2",
      "us-west-2",
      "eu-west-1",
      "ap-southeast-2",
      "ap-northeast-1",
      "us-gov-west-1",
    ], var.region)
    error_message = "Web Search Tool is only available in us-east-1, us-east-2, us-west-2, eu-west-1, ap-southeast-2, ap-northeast-1, us-gov-west-1."
  }
}

variable "name_prefix" {
  description = "Prefix applied to the names of created resources."
  type        = string
  default     = "websearch-poc"

  validation {
    condition     = can(regex("^[a-zA-Z0-9-]{1,32}$", var.name_prefix))
    error_message = "name_prefix must be 1-32 characters of letters, digits, or hyphens."
  }
}

variable "target_name" {
  description = <<-EOT
    Name of the gateway target. The Gateway exposes a target's tools as
    `<target_name>___<ToolName>`, so this value determines the tool name the
    calling agent sees: the default yields `web-search___WebSearch`.
  EOT
  type        = string
  default     = "web-search"

  validation {
    condition     = can(regex("^[a-zA-Z0-9-]{1,48}$", var.target_name))
    error_message = "target_name must be 1-48 characters of letters, digits, or hyphens."
  }
}

variable "connector_version" {
  description = <<-EOT
    Version of the AWS-managed `web-search` connector to pin. Version 1.2.0 adds
    the target-level domain *include* list plus per-request domain and
    published-date filters. 1.1.0 supports only the target-level exclude list.
  EOT
  type        = string
  default     = "1.2.0"

  validation {
    condition     = contains(["1.1.0", "1.2.0"], var.connector_version)
    error_message = "connector_version must be \"1.1.0\" or \"1.2.0\"."
  }
}

variable "domain_include" {
  description = <<-EOT
    Target-level domain allowlist. When non-empty, Web Search returns results
    ONLY from these domains (a root domain also matches its subdomains). This
    list is hidden from the calling agent and applied to every request, so no
    prompt injection can widen it. Requires connector_version 1.2.0.

    Empty by default so the default demo run is unfiltered. Set it to show the
    governance story, e.g. ["sec.gov", "reuters.com"].
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.domain_include) <= 100
    error_message = "domain_include accepts at most 100 domains."
  }
}

variable "domain_exclude" {
  description = <<-EOT
    Target-level domain denylist. Results from these domains are always dropped.
    Hidden from the calling agent and applied to every request. Works on both
    connector 1.1.0 and 1.2.0.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.domain_exclude) <= 100
    error_message = "domain_exclude accepts at most 100 domains."
  }
}

variable "mcp_supported_versions" {
  description = <<-EOT
    MCP protocol versions the Gateway will accept. Pinned explicitly so the demo
    client's `MCP-Protocol-Version` header is guaranteed to be accepted; the
    Gateway rejects versions absent from this list.
  EOT
  type        = list(string)
  default     = ["2025-11-25"]

  validation {
    condition     = length(var.mcp_supported_versions) > 0
    error_message = "mcp_supported_versions must contain at least one version."
  }
}

variable "existing_caller_principal_arn" {
  description = <<-EOT
    Optional ARN of an existing IAM user or role to grant `InvokeGateway` on the
    Gateway. Use this to call the Gateway with credentials you already have,
    instead of assuming the generated demo caller role.

    Leave empty to create only the demo caller role.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.existing_caller_principal_arn == "" || can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:(user|role)/", var.existing_caller_principal_arn))
    error_message = "existing_caller_principal_arn must be an IAM user or role ARN, or an empty string."
  }
}

variable "tags" {
  description = "Tags applied to all resources that support tagging."
  type        = map(string)
  default = {
    Project   = "agentcore-websearch-poc"
    ManagedBy = "terraform"
  }
}
