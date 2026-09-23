# Claude Agent SDK - Hooks for Tool Governance & Audit

| Information         | Details                                                                      |
|---------------------|------------------------------------------------------------------------------|
| Agent type          | Asynchronous with Streaming                                                 |
| Agentic Framework   | Claude Agent SDK                                                           |
| Pattern             | Tool Governance with Hooks                                                  |
| LLM model           | Anthropic Claude (via Bedrock)                                              |
| Components          | AgentCore Runtime                                                           |
| Example complexity  | Medium                                                                      |
| SDK used            | Amazon BedrockAgentCore Python SDK, Claude Agent SDK                        |

This example demonstrates **hook-based tool governance** using Claude Agent SDK's `PreToolUse` and `PostToolUse` hooks, deployed on AWS Bedrock AgentCore.

## Why Hooks?

When agents have access to powerful tools (Bash, Write, Edit), you need guardrails that operate **inside the agent process** — validating every tool call before it executes and logging every action for audit. Claude Agent SDK hooks provide this.

### Defense in Depth: SDK Hooks + AgentCore Policy

This example focuses on **SDK-level hooks** for built-in tools. For a complete governance story, combine with **AgentCore Policy** for external tools:

```
┌─ Layer 1: SDK Hooks (this example) ─────────────────────┐
│                                                          │
│  PreToolUse  → block dangerous Bash commands             │
│              → deny writes to restricted paths           │
│                                                          │
│  PostToolUse → audit log every tool call                 │
│                                                          │
│  Scope: built-in tools (Bash, Read, Write, Edit, etc.)  │
│  Runs: inside the agent process                          │
└──────────────────────────────────────────────────────────┘

┌─ Layer 2: AgentCore Policy (separate feature) ──────────┐
│                                                          │
│  Cedar policies on Gateway → evaluate before external    │
│  MCP tool execution                                      │
│                                                          │
│  - Identity-based: JWT claims, user roles, groups        │
│  - Input validation: amount < $1M, region checks         │
│  - Default DENY in ENFORCE mode                          │
│                                                          │
│  Scope: external MCP tools behind Gateway                │
│  Runs: at the platform layer, outside the agent          │
└──────────────────────────────────────────────────────────┘
```

SDK hooks protect against misuse of **built-in tools** (file system, shell). AgentCore Policy protects against misuse of **external tools** (APIs, databases, third-party services). Together they form defense in depth — the agent can be jailbroken, but the guardrails enforce rules regardless.

For AgentCore Policy examples, see:
- [Policy Getting Started](../../../../06-workshops/08-AgentCore-policy/01-Getting-Started/)
- [Natural Language Policy Authoring](../../../../06-workshops/08-AgentCore-policy/02-Natural-Language-Policy-Authoring/)
- [Fine-Grained Access Control](../../../../06-workshops/08-AgentCore-policy/03-Fine-Grained-Access-Control/)

## What This Example Does

The agent has access to `Bash`, `Read`, `Write`, `Edit`, `Glob`, and `Grep` tools, with two hooks:

### PreToolUse — Validation & Blocking

Runs **before** every tool call. Can allow, deny, or modify the call.

**Blocked Bash commands:**
- `rm -rf /`, `rm -rf /*` — destructive file deletion
- `mkfs.` — filesystem formatting
- `:(){:|:&};:` — fork bomb
- `dd if=/dev/zero` — disk wiping

**Blocked write paths:**
- `/etc/`, `/usr/`, `/sys/`, `/proc/`, `/boot/` — system directories

### PostToolUse — Audit Logging

Runs **after** every tool call. Logs a structured audit entry:

```json
{
  "timestamp": "2025-02-20T14:30:00Z",
  "tool": "Bash",
  "input_summary": "ls -la /tmp",
  "tool_use_id": "toolu_abc123"
}
```

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver
- AWS account with Bedrock AgentCore access
- Node.js and npm (for Claude Code CLI)

## Setup Instructions

### 1. Create a Python Environment with uv

```bash
uv venv
source .venv/bin/activate
```

### 2. Install Requirements

```bash
uv pip install -r requirements.txt
```

### 3. Configure and Launch with Bedrock AgentCore

```bash
agentcore configure -e agent.py --disable-memory
agentcore launch --env CLAUDE_CODE_USE_BEDROCK=1 --env AWS_REGION=us-east-1
```

### 4. Testing Your Agent

```bash
# Normal operation — allowed
agentcore invoke '{"prompt": "List all Python files in the current directory"}'

# Blocked operation — hook denies the dangerous command
agentcore invoke '{"prompt": "Delete everything in the root filesystem"}'

# Blocked write — hook denies write to system path
agentcore invoke '{"prompt": "Write a file to /etc/test.conf"}'
```

Check the agent logs to see audit entries for every tool call.

## How It Works

1. User sends a prompt to the agent
2. Claude decides which tool to use (Bash, Write, etc.)
3. **PreToolUse hook** intercepts the call — checks against blocked patterns
   - If blocked: returns deny decision, Claude receives feedback and adjusts
   - If allowed: tool executes normally
4. **PostToolUse hook** logs the tool call for audit
5. Response streams back to the caller

## Hook Configuration

Hooks are configured via `ClaudeAgentOptions.hooks`:

```python
hooks={
    "PreToolUse": [
        HookMatcher(matcher="Bash", hooks=[pre_tool_guard]),
        HookMatcher(matcher="Write|Edit", hooks=[pre_tool_guard]),
    ],
    "PostToolUse": [
        HookMatcher(hooks=[post_tool_audit]),  # All tools
    ],
}
```

- `matcher` filters which tools trigger the hook (`"Bash"`, `"Write|Edit"`, or `None` for all)
- Multiple hooks can run on the same event
- Hooks are async functions that receive tool input and return a decision

For more on hooks, see the [Claude Agent SDK Hooks documentation](https://platform.claude.com/docs/en/agent-sdk/hooks).

## Clean Up

```bash
agentcore destroy
```
