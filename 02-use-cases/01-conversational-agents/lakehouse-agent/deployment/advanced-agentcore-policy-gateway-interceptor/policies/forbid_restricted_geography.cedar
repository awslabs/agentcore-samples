forbid(
  principal,
  action in [
    AgentCore::Action::"lakehouse-mcp-target___query_claims",
    AgentCore::Action::"lakehouse-mcp-target___get_claim_details",
    AgentCore::Action::"lakehouse-mcp-target___get_claims_summary",
    AgentCore::Action::"lakehouse-mcp-target___query_login_audit",
    AgentCore::Action::"lakehouse-mcp-target___text_to_sql"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  context.input has geography &&
  context.input.geography == "RESTRICTED"
};
