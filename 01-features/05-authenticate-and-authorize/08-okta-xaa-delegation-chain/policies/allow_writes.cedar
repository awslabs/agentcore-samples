// Writes are allowed too, but scoped by an explicit claim check so the policy shows
// how per-user rules are written. Swap the condition for a group check to restrict
// writes to a subset of users, e.g.
//
//   principal.hasTag("groups") && principal.getTag("groups") like "*todo-editors*"
//
// Okta serialises array claims as JSON strings, so use `like` with wildcards rather
// than equality.
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"todo___add_todo",
    AgentCore::Action::"todo___complete_todo"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("sub")
};
