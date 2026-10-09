// Optional JWT/OAuth-aware authorization policy.
//
// Use this instead of, not in addition to, the IAM baseline when the Gateway
// has JWT/OAuth inbound authorization and the token includes the expected role
// claim. Do not use this policy for a Gateway that does not map the role claim
// to a principal tag.
permit(
    principal is AgentCore::OAuthUser,
    action == AgentCore::Action::"PaymentPolicyTools___authorize_payment",
    resource == AgentCore::Gateway::"<gateway-arn>"
)
when {
    principal.hasTag("role") &&
    principal.getTag("role") == "payment-buyer" &&
    context.input.resourceUrl == "<seller-resource-url>" &&
    context.input.payTo == "<seller-recipient>" &&
    context.input.network == "<network-id>" &&
    context.input.asset == "<asset-id>" &&
    context.input.amount <= 1000
};
