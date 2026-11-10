// Optional defense-in-depth guardrail for the same Gateway and tool.
//
// This forbid is redundant with the maximum in the baseline permit policy, but
// it continues to block an over-limit request if a later permit policy is
// broader than intended. Cedar evaluates forbid before permit.
forbid(
    principal,
    action == AgentCore::Action::"PaymentPolicyTools___authorize_payment",
    resource == AgentCore::Gateway::"<gateway-arn>"
)
when {
    context.input.amount > 1000
};
