forbid(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"lakehouse-mcp-target___get_claims_summary",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("cognito:groups") &&
  principal.getTag("cognito:groups") like "*policyholders*"
};
