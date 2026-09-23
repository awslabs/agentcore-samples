// ============================================================
// AgentCore Gateway Cedar Policies
// ============================================================
// These policies are evaluated by the AgentCore Policy Engine
// at the Gateway level — BEFORE requests reach the sandbox.
//
// This is the first line of defense. The sandbox's local Cedar
// engine (sandbox/policies/sandbox.cedar) provides a second layer.
//
// Default posture: DENY (Cedar default-deny semantics).
// The agent gets a structured deny reason and can adapt.
//
// Principal: AgentCore::OAuthUser (or IAM principal)
// Action:    AgentCore::Action::"<target>___<tool>"
// Resource:  AgentCore::Gateway::"<gateway-arn>"
// Context:   { input: { ...tool arguments... } }
// ============================================================

// --- PERMIT: Allow run_command with safe patterns ---
permit(
  principal,
  action == AgentCore::Action::"sandbox___run_command",
  resource
)
when {
  // Allow commands that don't match exfiltration patterns
  !(context.input.cmd like "*curl *") &&
  !(context.input.cmd like "*wget *") &&
  !(context.input.cmd like "*nc *") &&
  !(context.input.cmd like "*ncat *") &&
  !(context.input.cmd like "*ssh *") &&
  !(context.input.cmd like "*scp *") &&
  !(context.input.cmd like "*telnet *") &&
  !(context.input.cmd like "*socat *") &&
  // Reasonable timeout
  context.input.timeout <= 900
};

// --- PERMIT: Allow get_details unconditionally ---
permit(
  principal,
  action == AgentCore::Action::"sandbox___get_details",
  resource
);

// --- PERMIT: Allow write_file for safe paths ---
permit(
  principal,
  action == AgentCore::Action::"sandbox___write_file",
  resource
)
when {
  // Block path traversal and absolute system paths
  !(context.input.path like "../*") &&
  !(context.input.path like "*/../*") &&
  !(context.input.path like "/etc/*") &&
  !(context.input.path like "/proc/*") &&
  !(context.input.path like "/sys/*") &&
  !(context.input.path like "/dev/*")
};

// --- PERMIT: Allow read_file for safe paths ---
permit(
  principal,
  action == AgentCore::Action::"sandbox___read_file",
  resource
)
when {
  !(context.input.path like "../*") &&
  !(context.input.path like "*/../*") &&
  !(context.input.path like "/etc/shadow") &&
  !(context.input.path like "/proc/*") &&
  !(context.input.path like "/sys/*")
};

// --- FORBID: Hard deny for destructive operations ---
// These override any permits above (forbid-wins semantics).
forbid(
  principal,
  action == AgentCore::Action::"sandbox___run_command",
  resource
)
when { context.input.cmd like "*rm -rf /*" };

forbid(
  principal,
  action == AgentCore::Action::"sandbox___run_command",
  resource
)
when { context.input.cmd like "*sudo *" };

forbid(
  principal,
  action == AgentCore::Action::"sandbox___run_command",
  resource
)
when { context.input.cmd like "*> /dev/sd*" || context.input.cmd like "*mkfs*" };
