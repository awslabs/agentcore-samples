permit(
  principal,
  action == AgentCore::Action::"FileSystemTarget___read_file",
  resource == AgentCore::Gateway::"<gateway_arn>"
)
when {
  context.input has path &&
  context.input.path like "/workspace/src/*"
};

permit(
  principal,
  action == AgentCore::Action::"FileSystemTarget___write_file",
  resource == AgentCore::Gateway::"<gateway_arn>"
)
when {
  context.input has path &&
  context.input.path like "/workspace/src/*"
};

permit(
  principal,
  action == AgentCore::Action::"FileSystemTarget___list_directory",
  resource == AgentCore::Gateway::"<gateway_arn>"
)
when {
  context.input has path &&
  context.input.path like "/workspace/src/*"
};
