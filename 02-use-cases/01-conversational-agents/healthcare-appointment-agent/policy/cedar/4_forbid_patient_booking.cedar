// Policy 4: Forbid Rules — Hard Boundaries
//
// Patients are explicitly forbidden from booking appointments.
// In Cedar, forbid overrides permit — this is a hard boundary that
// cannot be bypassed by any other permit rule, even if the agent
// is manipulated into attempting a write operation.
//
// Replace {gateway_arn} with your actual Gateway ARN.

// Hard deny: patients cannot book appointments
forbid(
  principal,
  action == AgentCore::Action::"Target1___bookAppointment",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("role") &&
  principal.getTag("role") == "patient"
};
