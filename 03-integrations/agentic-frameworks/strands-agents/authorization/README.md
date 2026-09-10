# Strands sub-agent permissions with AgentCore Runtime

| Information | Details |
|---|---|
| Agent type | Synchronous |
| Agentic framework | Strands (agents as tools) |
| LLM model | Whatever the Runtime is configured with; the offline run uses a scripted model |
| Components | AgentCore Runtime |
| Example complexity | Easy |
| SDK used | Amazon BedrockAgentCore Python SDK |

An `operations_assistant` orchestrator hands a log investigation to a
`log_analyst` sub-agent, reached through Strands' agents-as-tools
pattern. The orchestrator may export findings to an external
destination. The sub-agent may not: it is delegated read access to logs
and nothing else. When it calls `export_findings` anyway, the call is
cancelled at the hook and the function is never entered.

The enforcement comes from [attenu-guard](https://github.com/attenu-io/attenu-guard),
an Apache-2.0 library (not part of the AWS SDKs). It ships a Strands
hook provider, `attenu_guard.adapters.strands.DelegationGuard`,
registered on each agent. Strands is unmodified, and nothing is
monkeypatched.

## Architecture

```
                    task
                     |
                     v
 caller ----> operations_assistant (holds: logs.read, logs.export,
              |         |            oncall.page, agent.delegate)
              |         |
              |         v  (Strands agents-as-tools)
              |     log_analyst (delegated: logs.read only,
              |         |        computed from the orchestrator's
              |         |        authority at hand-off time)
              |         |
              |         +--> read_logs()          allowed, runs
              |         +--> export_findings()     DENIED before the
              |                                    tool body runs
              v
   hash-chained ledger  ->  signed evidence bundle  ->  verified offline
   (attenu_guard.AuditLog)     (attenu_guard.evidence)   (no AWS, no network)
```

## What this sample shows

- **A sub-agent's permissions are computed from its parent's.** The
  child receives the meet of what it requests and what the parent holds,
  so a hand-off can only ever narrow. Requesting `iam.admin` from a
  parent that does not hold it yields nothing.
- **Refusal happens before the tool body runs.** The adapter sets
  `event.cancel_tool` on Strands' `BeforeToolCallEvent`, which makes the
  executor skip the body and hand the model an error result. Each tool in
  `agents.py` records its own entry as its first statement, which is how
  the tests tell "refused" apart from "ran, then reported".
- **Undeclared means denied.** A sub-agent with no entry in `SUB_AGENTS`
  cannot be delegated to; a tool with no entry in `SCOPE_FOR` resolves to
  `tool.<name>`, which no permission set grants. Both fail closed, and
  both land in the ledger with a reason code.
- **One permission model, two run paths.** `agents.py` and
  `permissions.py` import nothing from AWS. The Runtime entrypoint and
  the offline run both build their session from them, so what the tests
  exercise is what gets deployed.
- **A session per invocation.** The ledger, the delegation graph and the
  time-to-live belong to a single run. Sharing them across callers would
  blur whose decision was whose, so the entrypoint builds a fresh session
  each time.
- **The run leaves evidence.** Decisions append to a hash-chained
  ledger. `local_run.py` exports a signed bundle and verifies it with the
  packaged `attenu-guard verify` command — integrity, child-within-parent
  and containment — with no service and no network involved. The ledger
  is tamper-evident, not tamper-proof: a verifier detects an edit, it
  does not prevent one.

## Prerequisites

- Python 3.11 or newer
- [uv](https://github.com/astral-sh/uv)
- Nothing else for the offline run: no AWS account, no model access, no
  credentials.
- For the deploy path: an AWS account with Bedrock AgentCore access, and
  credentials in your environment.

## Setup

```bash
uv venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
uv pip install -r requirements-dev.txt
```

## Run it offline

```bash
python local_run.py
```

`ScriptedModel` is a `strands.models.Model` subclass that emits the
Bedrock-shaped stream events `strands.event_loop.streaming` consumes.
Strands' agent loop, hooks and tool executor are all the real ones; only
the model is substituted, so the run is deterministic, free and safe for
CI.

Tests:

```bash
python -m pytest
```

## Expected output

Abridged; the run prints the whole thing.

```text
2. what each agent holds
    operations_assistant: scopes=['agent.delegate', 'logs.export', 'logs.read', 'oncall.page']
    log_analyst: scopes=['logs.read']
    sub-agent is narrower than orchestrator: True

3. the refusal
    tool bodies that ran: [('read_logs', 1500)]
    DENIED {'agent': 'log_analyst', 'tool': 'export_findings',
            'scope': 'logs.export', 'reason': 'scope_not_granted'}
    nothing was written to s3://not-our-bucket/findings.json: True

5. the ledger, checked without this process
    5 events, hash chain: True
    integrity=True monotonicity=True containment=True anchor=verified
    OK

RESULT: OK
```

`export_findings` is absent from the list of tool bodies that ran.

## Understanding the entrypoint

`strands_attenu_guard.py` is the file you hand to `agentcore configure`.
It builds a session per invocation and returns the answer together with
the decisions from that run, so a caller sees what was refused without
reading the container's logs.

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents import build_session, denials, reset

app = BedrockAgentCoreApp()


@app.entrypoint
def agent_invocation(payload, context):
    prompt = payload.get("prompt", DEFAULT_PROMPT)

    reset()
    orchestrator, _analyst, guard = build_session(
        task=prompt, audit_path=LEDGER_PATH
    )
    result = orchestrator(prompt)

    return {
        "result": str(result.message),
        "denials": denials(guard),
        "ledger_events": len(guard.root_guard.audit_log().entries),
    }


if __name__ == "__main__":
    app.run()
```

`build_session(...)` with no models leaves Strands on its Bedrock
default, which is what the Runtime provides. The offline run passes
scripted models to the same function.

`ATTENU_GUARD_LEDGER` sets where the ledger is written inside the
container; it defaults to a path under `/tmp`. Point it at a mounted
volume, or export the bundle onward, if the record needs to outlive the
session.

## Deploy to AgentCore Runtime

```bash
agentcore configure -e strands_attenu_guard.py
agentcore launch
agentcore invoke '{"prompt": "Investigate the 5xx spike overnight."}'
```

`input.json` holds the same payload if you prefer to pass a file.

These three commands are the standard Runtime flow and are written from
the SDK's documented shape. They have **not** been executed for this
sample: it was built and checked without an AWS account, so the offline
run, the tests and the entrypoint's handler contract are verified, and
the deployed round trip is not. Expect to adjust the IAM role and the
region for your own account.

## Clean up

The offline run and the tests create nothing outside a temp directory
under your system's temp folder — there is nothing to tear down. If you
run the deploy path above, remove the resources `agentcore launch`
created (the AgentCore Runtime endpoint and its IAM role) from your AWS
account when you're done, e.g. via the AWS console or `agentcore
destroy` if your toolkit version provides it.

## Trust boundary

The adversary this addresses is the agent itself — a sub-agent steered by
a poisoned log line, a confused plan, or a misleading tool description
into asking for something outside its remit. The enforcement point runs
in-process, inside the same container as the agent, and holds:

- as long as the hook is registered on every agent. `build_session()`
  does that and then calls `require_guard()`, which turns a mis-wired
  guard into a startup failure rather than a run nobody was checking.
- for anything routed through Strands' tool executor. Code that reaches
  a side effect without going through a tool call is outside the checked
  path.
- against permissions, not against content. The library takes no view on
  whether exporting the findings is a good idea; it holds the analyst to
  what it was delegated.

It does not defend against an attacker with code execution in the
container, who can edit `permissions.py` before it is loaded. Exported
evidence is verified against a public key, so a bundle altered after
export fails verification with the key alone.

Writing the permission model is your job, deliberately: `permissions.py`
is a short, reviewable file, and the library enforces exactly what it
says.

## Files

| Path | What it holds |
|---|---|
| `permissions.py` | The three declarations: what the orchestrator holds, what the sub-agent may request, what each tool needs |
| `agents.py` | The agents, the tools, `build_session()`, `require_guard()` |
| `strands_attenu_guard.py` | The AgentCore Runtime entrypoint |
| `local_run.py` | The scripted-model run, the evidence export, the offline verification |
| `input.json` | A sample payload for `agentcore invoke` |
| `tests/` | Enforcement assertions plus the entrypoint's handler contract |

Versions this was checked against: `strands-agents` 1.54.0,
`bedrock-agentcore` 1.22.0, `attenu-guard` 0.8.0, Python 3.14.6. The
`bedrock-agentcore-starter-toolkit` (0.3.12) provides the `agentcore`
CLI and was not exercised.

## Disclaimer

The examples provided in this repository are for experimental and
educational purposes only. They demonstrate concepts and techniques but
are not intended for direct use in production environments. Make sure to
have Amazon Bedrock Guardrails in place to protect against [prompt
injection](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-injection.html).

## Licence

Apache-2.0, matching this repository and the [attenu-guard](https://github.com/attenu-io/attenu-guard) library.
