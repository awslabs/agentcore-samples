// ============================================================
// Sandbox Cedar Policies — Local enforcement layer
// ============================================================
// These policies govern what the coding agent can do via the sandbox.
// Evaluated by cedarpy BEFORE any action executes.
// Default posture: deny unless explicitly permitted.
//
// Action types:
//   Action::"run_command"  — shell command execution
//   Action::"write_file"   — file write operations
//   Action::"read_file"    — file read operations
//   Action::"get_details"  — environment inspection (always allowed)
//
// Context fields (depend on action):
//   context.cmd      — the command string (run_command)
//   context.cwd      — working directory (run_command)
//   context.path     — file path (write_file, read_file)
//   context.timeout  — command timeout in seconds (run_command)
// ============================================================

// --- HARD DENY: Network exfiltration tools ---
// These cannot be bypassed regardless of other permits.
forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*curl *" || context.cmd like "*curl\"*" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*wget *" || context.cmd like "*wget\"*" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*nc *" || context.cmd like "*ncat *" || context.cmd like "*netcat *" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*socat *" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*telnet *" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when {
  context.cmd like "*ssh *" ||
  context.cmd like "*scp *" ||
  context.cmd like "*sftp *" ||
  context.cmd like "*rsync *"
};

// --- HARD DENY: Dangerous destructive commands ---
forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*rm -rf /*" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*mkfs*" || context.cmd like "*dd if=*of=/dev*" };

// --- HARD DENY: Privilege escalation ---
forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*sudo *" || context.cmd like "*su -*" };

forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*chmod 777*" || context.cmd like "*chmod +s*" };

// --- HARD DENY: Path traversal in file operations ---
forbid(
  principal,
  action == Action::"write_file",
  resource
)
when { context.path like "../*" || context.path like "*/../*" };

forbid(
  principal,
  action == Action::"read_file",
  resource
)
when { context.path like "../*" || context.path like "*/../*" };

// --- HARD DENY: Absolute path escape in file operations ---
forbid(
  principal,
  action == Action::"write_file",
  resource
)
when { context.path like "/etc/*" || context.path like "/proc/*" || context.path like "/sys/*" };

forbid(
  principal,
  action == Action::"read_file",
  resource
)
when { context.path like "/etc/*" || context.path like "/proc/*" || context.path like "/sys/*" };

// --- HARD DENY: Command timeout abuse ---
forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.timeout > 900 };

// --- PERMITS: Normal operations ---
// Allow run_command (anything not explicitly forbidden above)
permit(
  principal,
  action == Action::"run_command",
  resource
);

// Allow write_file (anything not forbidden)
permit(
  principal,
  action == Action::"write_file",
  resource
);

// Allow read_file (anything not forbidden)
permit(
  principal,
  action == Action::"read_file",
  resource
);

// Always allow get_details (no side effects)
permit(
  principal,
  action == Action::"get_details",
  resource
);
