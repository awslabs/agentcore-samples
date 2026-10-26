// Any signed-in Okta user may read their own todos and see who they are.
//
// `principal is AgentCore::OAuthUser` matches because the gateway's inbound token is
// a JWT: Cedar surfaces its claims as principal TAGS. This keeps working even though
// the REQUEST interceptor replaces Authorization before the request reaches the API,
// because the interceptor runs first and Cedar still evaluates the inbound identity.
//
// hasTag("sub") is the guard: if the claims were ever absent the permit would not
// match, and Cedar's default-deny would refuse the call rather than fail open.
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"todo___list_todos",
    AgentCore::Action::"todo___whoami"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("sub")
};
