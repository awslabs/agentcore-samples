forbid(
  principal,
  action in [
    AgentCore::Action::"lakehouse-mcp-target___query_claims",
    AgentCore::Action::"lakehouse-mcp-target___get_claim_details"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  context.input has geography &&
  context.input.geography == "EU"
};
