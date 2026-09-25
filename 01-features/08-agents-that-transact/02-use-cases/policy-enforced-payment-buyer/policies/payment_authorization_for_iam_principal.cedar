// Baseline policy for the SigV4-signed Gateway E2E runner.
//
// Replace every <...> value with a value from your own deployment. For an
// AWS_IAM Gateway, principal.id contains the caller's IAM ARN.
//
// Keep the caller, resource, recipient, network, asset, and amount constraint
// in this same permit statement. Separate permit statements are alternatives,
// not cumulative requirements.
permit(
    principal is AgentCore::IamEntity,
    action == AgentCore::Action::"PaymentPolicyTools___authorize_payment",
    resource == AgentCore::Gateway::"<gateway-arn>"
)
when {
    principal.id == "<caller-iam-arn>" &&
    context.input.resourceUrl == "<seller-resource-url>" &&
    context.input.payTo == "<seller-recipient>" &&
    context.input.network == "<network-id>" &&
    context.input.asset == "<asset-id>" &&
    context.input.amount <= 1000
};
