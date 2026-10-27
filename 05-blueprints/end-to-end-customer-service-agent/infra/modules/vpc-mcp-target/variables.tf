variable "gateway_name" {
  description = "Name for the AgentCore Gateway"
  type        = string
  default     = "cx-entra-obo-gateway"
}

variable "entra_tenant_id" {
  description = "Microsoft Entra ID Directory (tenant) ID"
  type        = string
}

variable "entra_agent_client_id" {
  description = "Entra ID Application (client) ID for the agent/gateway app (App 1)"
  type        = string
}

variable "entra_mcp_client_id" {
  description = "Entra ID Application (client) ID for the MCP Server app (App 2 — audience)"
  type        = string
}
