# AGENTS.md -- Assistant Runbook

This file is for the coding assistant (Claude Code, Codex, Cursor, Kiro). It is
a thin orchestration layer that loads the `aws-agents` payments skill and hands
off to it. Do not reinvent the skill's content -- load it and follow it.

## Developer prompt contract

This runbook supports exactly two scenarios:

- **Existing agent** -- add AgentCore x402 payments to an agent project.
- **New agent** -- scaffold a new agent, then add AgentCore x402 payments to it.

A short request such as "use this folder to add AgentCore payments" is sufficient.
If the scenario (existing vs new) cannot be determined from the prompt or
workspace, ask one routing question before taking action.

Good minimal prompts:

```text
Use this folder to add AgentCore payments to my existing agent.
```

```text
Use this folder to create a new AgentCore payments agent.
```

## 0. Load the skill

The skill must be loaded before any provisioning or payment steps. This is the
one thing only the orchestration handles -- the skill cannot verify its own loading.

### Plugin-capable tools (Claude Code, Codex, Cursor)

Invoke the `payments` capability under the `agents-build` skill via the
assistant's native plugin route. After loading, state which route is being used:

```text
Using aws-agents <AssistantName> plugin payments skill.
```

If the plugin is not installed or not visible in the current session, stop and
tell the developer:

```text
The aws-agents plugin is not installed or not visible in this session.
Install it per the README prerequisites and start a fresh session.
```

Do not use the cloned fallback for plugin-capable assistants unless the developer
explicitly requests it.

### Fallback (tools without plugin support, e.g. Kiro)

Read the skill directly from the local clone:

```text
agent-toolkit-for-aws/plugins/aws-agents/skills/agents-build/references/payments.md
```

State that the fallback is being used:

```text
Using cloned payments.md fallback because <AssistantName> has no aws-agents plugin route.
```

If neither route is available, stop and direct the developer to
[`README.md`](README.md) -> "Prerequisites." Do not proceed from memory.

### Plugin not available

If the plugin is not available, attempt to install it automatically:

- **Claude Code:** `/plugin install aws-agents@claude-plugins-official` then `/reload-plugins`
- **Codex:** `codex plugin marketplace add aws/agent-toolkit-for-aws` then install `aws-agents`
- **Cursor:** Import `aws/agent-toolkit-for-aws` as a team marketplace, install `aws-agents`

If the install succeeds, tell the developer:

```text
Plugin installed. Start a new session in this folder to continue -- the plugin
loads at session startup and is not visible until then.
```

Then stop. Do not continue the payment flow in the same session.

If the install fails (timeout, network error, or unsupported tool), stop and
direct the developer to the README prerequisites for manual installation.

## 1. Run the skill

Hand the developer's request to the skill's Process section and **run it from
Step 0 through Step 8**. Do not skip steps. The skill handles:

- CLI verification (Step 0)
- Project detection or scaffolding (Step 1)
- Situation routing -- detecting existing managers, deciding what to create vs
  reuse (Step 2)
- Provisioning manager and connector (Step 3)
- Deploy (Step 4)
- Wiring the agent -- native plugin or generic tool (Step 5)
- Instrument and session creation (Step 6)
- Delegation and funding (Step 7)
- Env vars and test (Step 8)

Trust the skill's routing logic. If a manager already exists, the skill detects
it and skips creation. Do not force creation of new resources.

## 2. Human checkpoints -- pause and wait

The skill defines two points where you must hand control to the developer. Never
handle credentials, never assume funding happened, never read `agentcore/.env.local`.

**Checkpoint A -- provider credentials (skill Step 3b).**

The developer runs `agentcore add payment-connector` (no flags -> interactive
wizard). Present the prerequisites, then wait for confirmation. After they
confirm, collect the user id and email for the first wallet, then continue.

**Checkpoint B -- delegation and funding (skill Step 7).**

Surface the `wallet_address` / `redirect_url` from the setup script output. Wait
until the developer confirms the instrument is active and funded.

## 3. Cleanup

```bash
agentcore remove payment-connector --manager <ManagerName> --name <ConnectorName> -y
agentcore remove payment-manager --name <ManagerName> -y
agentcore deploy -y
```
