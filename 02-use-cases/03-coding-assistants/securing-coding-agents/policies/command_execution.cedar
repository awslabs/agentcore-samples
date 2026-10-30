forbid(
  principal,
  action == AgentCore::Action::"ShellTarget___execute",
  resource == AgentCore::Gateway::"<gateway_arn>"
)
when {
  context.input has command &&
  (context.input.command like "rm -rf*" ||
   context.input.command like "sudo*" ||
   context.input.command like "chmod 777*")
};

permit(
  principal,
  action == AgentCore::Action::"ShellTarget___execute",
  resource == AgentCore::Gateway::"<gateway_arn>"
)
when {
  context.input has command &&
  (context.input.command == "npm test" ||
   context.input.command == "npm run build" ||
   context.input.command == "python -m pytest")
};
