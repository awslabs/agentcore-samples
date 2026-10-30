// Deny writes to a named user, overriding the permit in allow_writes.cedar.
//
// This is the policy that proves per-user authorization actually enforces, rather than
// merely permitting: Cedar `forbid` always beats `permit`, so this user can still read
// their todos but cannot add or complete one.
//
// It reads the `sub` claim from the token the GATEWAY validated -- surfaced as a
// principal TAG -- which is the inbound token, not the one the interceptor injects
// afterwards. That is what makes per-user policy compatible with credential injection.
//
// Set CEDAR_READONLY_USER in .env to the email in the access token (note: the ID token
// carries the Okta user id instead, so use the email). Leave it unset and this policy
// is not deployed.
forbid(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"todo___add_todo",
    AgentCore::Action::"todo___complete_todo"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("sub") &&
  principal.getTag("sub") == "{readonly_user}"
};
