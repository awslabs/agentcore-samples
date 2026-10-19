permit(
  principal is AgentCore::IamEntity,
  action == AgentCore::Action::"WebSearch___WebSearch",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:{{REGION}}:{{ACCOUNT_ID}}:gateway/{{GATEWAY_ID}}"
)
when {
  principal.id like "*AWSReservedSSO_ClaudeCode-*"
};
