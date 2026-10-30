forbid(
  principal,
  action in [
    AgentCore::Action::"CodeTarget___python_repl",
    AgentCore::Action::"CodeTarget___execute_code"
  ],
  resource == AgentCore::Gateway::"<gateway_arn>"
);

forbid(
  principal,
  action == AgentCore::Action::"RetrieveTarget___retrieve",
  resource == AgentCore::Gateway::"<gateway_arn>"
);

forbid(
  principal,
  action == AgentCore::Action::"HttpTarget___http_request",
  resource == AgentCore::Gateway::"<gateway_arn>"
);
