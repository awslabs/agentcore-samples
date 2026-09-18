# Claude Code with Gateway inference targets

Deploys Claude Code on Amazon Bedrock AgentCore Runtime with all model traffic routed
through an AgentCore Gateway inference target instead of calling Amazon Bedrock
directly from the container.

The container holds no Bedrock credentials. It authenticates to the gateway as a
workload, using an OAuth 2.0 client-credentials grant against Amazon Cognito, and the
gateway's IAM role is the only identity that reaches Bedrock. The agent's execution
role can read exactly one Secrets Manager secret (the OAuth client credentials) and
nothing else.

## Architecture

```
InvokeAgentRuntime
        │
        ▼
┌──────────────────────────────┐
│  AgentCore Runtime           │
│  (Claude Code, headless)     │
│                              │
│  1. read OAuth secret ───────┼──▶ Secrets Manager (execution role,
│  2. mint token ──────────────┼──▶ Cognito /oauth2/token   one secret)
│  3. claude -p "<prompt>"     │
│     ANTHROPIC_AUTH_TOKEN     │
└──────────────┬───────────────┘
               │ Anthropic Messages API + Bearer JWT
               ▼
┌──────────────────────────────┐
│  AgentCore Gateway           │
│  CUSTOM_JWT authorizer       │
│                              │
│  inference target "mantle"   │
│  (bedrock-mantle connector)  │
└──────────────┬───────────────┘
               │ gateway IAM role (SigV4)
               ▼
        Amazon Bedrock
```

The container never sees an AWS credential that can reach Bedrock. If the container is
compromised, the blast radius is one OAuth client credential, which can be revoked at
the user pool without touching IAM.

## Prerequisites

- AWS account with access to Amazon Bedrock AgentCore and the desired Claude models
- AWS CLI configured with credentials
- Python 3.10+ and `boto3` recent enough to support inference gateway targets
  (`pip install --upgrade boto3`)
- Docker with buildx (the image is built for ARM64, which AgentCore Runtime requires)

## Step-by-step guide

### Step 1 — Provision the gateway

```bash
python setup.py --region us-east-1
```

Creates a Cognito user pool with a `client_credentials` app client, an IAM role for
the gateway, the gateway itself with a CUSTOM_JWT authorizer, and one inference target
named `mantle` using the `bedrock-mantle` connector. Resource ids are recorded in
`.provision-state.json` as they are created, and the ready-to-use Claude Code
environment is printed when provisioning finishes. Re-running `setup.py` against a
half-finished provision resumes it.

The connector configures operations, model discovery, and model-id translation
automatically. The gateway authenticates outbound to Bedrock with its own IAM role
(`GATEWAY_IAM_ROLE`); no provider secret is stored.

### Step 2 — Verify capabilities through the gateway

```bash
python verify.py
```

Sends real Messages API requests through the gateway and checks the response signals
that prove each capability worked: 

- **caching** — a `cache_control` prefix reports `cache_creation_input_tokens > 0` on
  the first call and `cache_read_input_tokens > 0` on an identical second call
- **reasoning** — extended thinking engages and reports nonzero thinking tokens
- **multimodal** — the model names the colour of an embedded PNG
- **tool use** — a tool definition round-trips and the model answers with a
  `tool_use` block

This step needs only the gateway from Step 1, not the runtime. It costs roughly six
model calls. For models older than Claude Opus 4.7, pass `--thinking-form enabled`.

### Step 3 — Deploy the agent

```bash
python deploy.py --region us-east-1
```

Builds the ARM64 image, pushes it to a new ECR repository, stores the OAuth client
credentials in a Secrets Manager secret, creates an execution role scoped to that one
secret plus ECR pull and CloudWatch Logs, and creates the AgentCore Runtime. The
container environment carries only the secret's id, never the credential values.

### Step 4 — Invoke the agent

```bash
python invoke.py 'Reply with exactly the word: ok'
```

For a turn that proves the agent actually works end to end (tool use through the
gateway, not just connectivity):

```bash
python invoke.py 'Create a file named answer.txt containing the word gateway, then read it back and reply with its contents.'
```

Watch the agent's logs, including per-turn timing and any failure detail:

```bash
python logs.py
```

### Step 5 — Update after a change

```bash
python update.py --region us-east-1
```

Rebuilds and rolls the existing runtime onto the new image without changing its ARN.
This is needed because a runtime pins the image digest it resolved at create time, so
pushing a new `:latest` alone does nothing.

### Step 6 — Cleanup

```bash
python cleanup.py
```

Deletes every recorded resource from both state files, runtime stack first and in
reverse creation order, including the runtime's CloudWatch log group, which AgentCore
creates implicitly and which holds prompt text. Anything that fails to delete stays in
its state file so the run can be retried. If the gateway state file was lost,
`python cleanup.py --discover` finds gateway resources by name prefix.

## How Claude Code is configured for the gateway

Three settings in the image (`settings.json` and `agent_server.py`) matter if you adapt
this sample:

- **`ANTHROPIC_AUTH_TOKEN`** carries the gateway token. The gateway returns a 401 for a
  request carrying both `Authorization` and `x-api-key`, which is what Claude Code's
  `apiKeyHelper` sends, so the server mints a token per invocation and sets this
  variable instead.
- **`CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`** is required. Claude Code sends
  experimental `anthropic-beta` flags that Bedrock rejects, and the gateway passes the
  rejection through as `400 invalid beta flag`. Core capabilities (tool use, prompt
  caching, streaming) are unaffected.
- **`ANTHROPIC_MODEL`** and **`ANTHROPIC_DEFAULT_HAIKU_MODEL`** take target-qualified
  ids such as `mantle/anthropic.claude-opus-4-7`, where the part before the slash names
  the gateway target. Change them in `deploy.py` (`runtime_env`).

## Request and response format

The runtime implements the AgentCore Runtime HTTP contract:

```
POST /invocations
{"prompt": "..."}
    -> 200 {"response": "<claude stdout>", "status": "success"}
    -> 500 {"error": "agent run failed, see runtime logs", "status": "error"}

GET /ping
    -> {"status": "Healthy"} or {"status": "HealthyBusy", "time_of_last_update": ...}
```

Failure detail goes to CloudWatch. Response bodies can reach caller-side logs, so they
carry no infrastructure identifiers.

## Notes for production

- A token is minted per invocation, which is sufficient because each turn is a
  short-lived `claude -p` process. A long-running session that outlives the token
  would need in-container token refresh.
- The inference target here uses the `bedrock-mantle` connector, which is the
  zero-configuration path. To restrict which models callers can use, switch to an
  explicit provider configuration; see the
  [llm-inference tutorials](../../../../07-centralize-and-govern-your-ai-infrastructure/01-gateway/01-attach-targets/llm-inference).
- `setup.py` uses boto3 rather than CloudFormation because
  `AWS::BedrockAgentCore::GatewayTarget` does not yet support `Inference` target
  configurations.
