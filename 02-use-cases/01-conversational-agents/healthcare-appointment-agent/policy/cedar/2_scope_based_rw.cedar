// Policy 2: Scope-Based Read/Write Separation
//
// OAuth scopes control which tools are available:
//   - healthcare.read  → read tools (getPatient, searchImmunization, getSlots)
//   - healthcare.write → write tools (bookAppointment)
//
// The JWT "scope" claim is a space-separated string. Cedar's `like`
// operator with wildcards matches substrings within it.
//
// Note: Cognito scope names use dots (healthcare.read) because
// Cognito scope names cannot contain forward slashes.
//
// Replace {gateway_arn} with your actual Gateway ARN.

// Tokens with healthcare.read scope can use read tools
permit(
  principal,
  action in [
    AgentCore::Action::"Target1___getPatient",
    AgentCore::Action::"Target1___searchImmunization",
    AgentCore::Action::"Target1___getSlots"
  ],
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("scope") &&
  principal.getTag("scope") like "*healthcare.read*"
};

// Tokens with healthcare.write scope can book appointments
permit(
  principal,
  action == AgentCore::Action::"Target1___bookAppointment",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("scope") &&
  principal.getTag("scope") like "*healthcare.write*"
};
