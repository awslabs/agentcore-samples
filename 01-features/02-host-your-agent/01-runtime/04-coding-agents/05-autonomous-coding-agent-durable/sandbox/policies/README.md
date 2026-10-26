# Sandbox Cedar Policies

This directory contains Cedar policy files that govern what the coding agent can do via the sandbox runtime.

## How it works

1. Every sandbox action (run_command, write_file, read_file, get_details) is evaluated against Cedar policies **before** execution
2. If the policy engine returns DENY, the action is blocked and the agent receives a structured reason
3. The agent can adapt based on the denial reason (e.g., use `pip install` instead of `curl`)

## Policy modes

Set via `CEDAR_POLICY_MODE` environment variable:

- **ENFORCE** (default): Denied actions are blocked
- **AUDIT**: Denied actions are logged but allowed (for rollout testing)

## Files

- `sandbox.cedar` — The active policy set for local enforcement

## Updating policies

Policies can be updated without redeploying the container by mounting an updated file. The policy engine reloads when it detects a file modification (mtime change).

## Testing policies

```bash
# Run the Cedar policy test suite
python3 -m pytest tests/test_cedar_policy.py -v
```

## Adding new rules

Cedar uses forbid-overrides-permit semantics:
- `forbid` rules block actions absolutely (cannot be overridden by permits)
- `permit` rules allow actions (only if no forbid matches)
- If no rule matches: DENY (default-deny posture)

Example — block a new dangerous command:
```cedar
forbid(
  principal,
  action == Action::"run_command",
  resource
)
when { context.cmd like "*dangerous_tool *" };
```
