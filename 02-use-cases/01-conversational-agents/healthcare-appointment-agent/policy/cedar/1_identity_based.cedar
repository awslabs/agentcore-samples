// Policy 1: Identity-Based Access
//
// Patients can only read their own records. The tool input parameter
// (patient_id or search_value) must match the authenticated user's
// patient_id claim from the JWT token.
//
// Replace {gateway_arn} with your actual Gateway ARN.

// Patient can read their own patient record
permit(
  principal,
  action == AgentCore::Action::"Target1___getPatient",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("role") &&
  principal.getTag("role") == "patient" &&
  context.input has patient_id &&
  principal.hasTag("patient_id") &&
  context.input.patient_id == principal.getTag("patient_id")
};

// Patient can search their own immunization records
permit(
  principal,
  action == AgentCore::Action::"Target1___searchImmunization",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {
  principal.hasTag("role") &&
  principal.getTag("role") == "patient" &&
  context.input has search_value &&
  principal.hasTag("patient_id") &&
  context.input.search_value == principal.getTag("patient_id")
};
