# Codex on AgentCore Runtime with EFS

Deploys the [OpenAI Codex SDK](https://www.npmjs.com/package/@openai/codex-sdk) as an HTTP agent on AWS Bedrock AgentCore Runtime, with an EFS file system mounted at `/mnt/efs` for persistent storage shared across sessions.

Codex runs against models served by Amazon Bedrock through the runtime's IAM execution role. No OpenAI API key is used.

## Architecture

```
  ┌─────────────────────────┐         ┌─────────────────────────┐
  │  AgentCore Runtime      │         │  AgentCore Runtime      │
  │  Session A              │         │  Session B              │
  │  (Codex SDK)            │         │  (Codex SDK)            │
  │                         │         │                         │
  │  /mnt/efs ─────-────────┼────┐    │  /mnt/efs ─────-────────┼────┐
  └─────────────────────────┘    │    └─────────────────────────┘    │
                                 │                                   │
                                 ▼                                   ▼
                    ┌──────────────────────────────────────────────────┐
                    │  EFS File System (encrypted, generalPurpose)     │
                    │                                                  │
                    │  ┌────────────────────────┐                      │
                    │  │  EFS Access Point      │                      │
                    │  │  (uid/gid 1000,        │                      │
                    │  │   root /codex)         │                      │
                    │  └────────────────────────┘                      │
                    │                                                  │
                    │  /mnt/efs/.codex/        CODEX_HOME (threads,     │
                    │                          config.toml)            │
                    │  /mnt/efs/.codex/skills/ shared skills (writable)│
                    │  /mnt/efs/workspace/     git repo Codex edits    │
                    └──────────────────────────────────────────────────┘
```

Multiple runtime sessions mount the same EFS file system, enabling agents to share skills, results, and data across independent invocations.

Because `CODEX_HOME` itself lives on EFS, a **Codex thread** is persistent state, not session state. A thread started in Session A can be resumed from Session B — see Step 3.

```
CloudFormation stack (cfn-vpc.yaml):
  VPC, subnets, NAT Gateway, Security Group
  EFS file system, access point, mount targets

deploy.py creates:
  IAM execution role (Bedrock, ECR, EFS, logs, metrics)
  AgentCore Runtime (container from ECR, EFS mounted at /mnt/efs)
```

## Prerequisites

- AWS credentials in your shell with permission to create VPC, EFS, ECR, IAM and AgentCore resources
- Bedrock access to a GPT-5.6 model (default `openai.gpt-5.6-terra`). See [Model availability](#model-availability).
- Docker with buildx (the runtime requires `linux/arm64`)

> **Cost:** Step 1 provisions a NAT Gateway, an Elastic IP and an EFS file system. These bill hourly whether or not you invoke the agent, so run [Step 5](#step-5--cleanup) when you are done.

### Python environment

Use a fresh virtualenv rather than a system boto3. Mounting EFS relies on the
`filesystemConfigurations` parameter of `create_agent_runtime`, which older
boto3 releases do not know about; `deploy.py` fails with a clear message if it
detects one.

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install boto3 awscli --force-reinstall --no-cache-dir
```

## Step-by-step guide

### Step 1 — Infrastructure setup (CloudFormation)

Run the setup script to deploy the CloudFormation stack (VPC, subnets, NAT Gateway, Security Group, EFS), build the arm64 Docker image, and push it to ECR.

```bash
./setup.sh us-west-2
```

All outputs are saved to `envvars.config` and used automatically by the next steps.

### Step 2 — Deploy the agent

Create the IAM execution role and the AgentCore Runtime:

```bash
python deploy.py
```

The script waits until the runtime status is `READY` and saves the runtime config to `runtime_config.json`.

To use a different Bedrock model:

```bash
CODEX_MODEL=openai.gpt-5.6-luna python deploy.py
```

If you need to update an existing runtime (e.g. after rebuilding the Docker image), run:

```bash
python update.py
```

### Step 3 — Invoke the agent

Send a prompt to the deployed agent. The response includes both a `_runtimeSessionId` (the container session) and a `threadId` (the Codex conversation, persisted on EFS).

**Session A** — create a shared skill on the persistent filesystem:

```bash
python invoke.py "can u create a new skill, to review python code? This skill should be created into /mnt/efs/.codex/skills/"
```

`/mnt/efs/.codex/skills/` is Codex's native skills directory, so a skill written here is not just a file on a shared disk — it is advertised to every later session as an available skill.

Continue the conversation within the same session:

```bash
python invoke.py --session <session-a-id> "now add unit tests for that skill"
```

**Session B** — a completely new session accesses the same filesystem and uses the skill created by Session A:

```bash
python invoke.py "list the skills available in /mnt/efs/.codex/skills/ and use the python review skill to review this code: def add(a,b): return a+b"
```

Both sessions share `/mnt/efs`, so anything written by one session is immediately available to others. Note that all of `/mnt/efs` is *readable*, but only `WORKSPACE_DIR` and `SKILLS_DIR` are writable — see [How Codex is configured for Bedrock](#how-codex-is-configured-for-bedrock).

**Resume a Codex thread from a brand new session.** Because `CODEX_HOME` is on EFS, the conversation itself survives the session that created it:

```bash
python invoke.py --thread <codex-thread-id> "what did we work on earlier?"
```

This is the key difference from session-scoped agents: `--session` resumes the container, `--thread` resumes the conversation.

### Step 4 — Execute a command on the running session

Run a shell command directly on the container using the session ID from the previous step:

```bash
python exec_cmd.py --session <session-id> "ls -l /mnt/efs"
python exec_cmd.py --session <session-id> "ls -l /mnt/efs/.codex/sessions"
```

### Step 5 — Cleanup

Delete all AgentCore resources (runtime, IAM role), the ECR repository, and the CloudFormation stack.

```bash
python cleanup.py
```

Or use the shell wrapper:

```bash
./cleanup.sh
```

Deleting the stack deletes the EFS file system and every Codex thread stored on it.

Stack deletion can fail with `DELETE_FAILED` on the private subnets. AgentCore
releases the network interfaces it attached to them only after the runtime is
gone, and until it does the subnets still have dependencies. Those interfaces
are service-owned, so they cannot be detached by hand. The NAT Gateway and EFS
file system are deleted before this point, so the expensive resources are
already released; wait for the interfaces to disappear and rerun the delete:

```bash
aws ec2 describe-network-interfaces --region <region> \
    --filters Name=subnet-id,Values=<private-subnet-1> \
    --query 'NetworkInterfaces[].NetworkInterfaceId'
aws cloudformation delete-stack --stack-name agentcore-codex-demo --region <region>
```

## How Codex is configured for Bedrock

Codex reads its provider configuration from `$CODEX_HOME/config.toml`. `server.js` rewrites the block it owns on every boot, so changing `CODEX_MODEL` and redeploying takes effect even though `CODEX_HOME` is on EFS and outlives the container. Anything Codex itself appends to the file (such as `[projects."..."] trust_level`) is preserved below the managed block:

```toml
# ── managed by server.js: rewritten on every boot ──
model_provider = "amazon-bedrock"
model = "openai.gpt-5.6-terra"
model_reasoning_effort = "medium"
check_for_update_on_startup = false

[model_providers.amazon-bedrock.aws]
region = "<BEDROCK_REGION>"

[otel]
exporter = "none"
metrics_exporter = "none"
trace_exporter = "none"
log_user_prompt = false
# ── end managed ──
```

Four details matter:

- **Credentials come from the execution role.** `server.js` strips `OPENAI_API_KEY`, `CODEX_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, and `AWS_PROFILE` from the environment it hands to Codex, so the container cannot silently fall back to a different identity.
- **The workspace must be a git repository.** Codex refuses to run with `skipGitRepoCheck: false` outside a repo, so `server.js` runs `git init -b main` in `WORKSPACE_DIR` on first boot. This is what gives Codex a diff to reason about.
- **The provider talks to `bedrock-mantle`, which needs extra IAM.** Codex's `amazon-bedrock` provider calls the OpenAI-compatible `bedrock-mantle` endpoint (`https://bedrock-mantle.<region>.api.aws/openai/v1/responses`), not `bedrock-runtime:InvokeModel`. `deploy.py` therefore grants `bedrock-mantle:CreateInference` and `bedrock-mantle:CallWithBearerToken` — the two actions Codex actually calls. The `AmazonBedrockMantleInferenceAccess` managed policy is a broader alternative if you prefer a managed grant.
- **Writes outside the workspace must be granted explicitly.** `sandboxMode: "workspace-write"` makes only `WORKSPACE_DIR` writable, and `approvalPolicy: "never"` means Codex cannot ask for more. `server.js` passes `additionalDirectories: [SKILLS_DIR]` so the shared skills directory is writable too; anything else under `/mnt/efs` is readable but not writable.

## Model availability

Because the `amazon-bedrock` provider only reaches the `bedrock-mantle` endpoint, `CODEX_MODEL` must be a **GPT-5.6** model. Other Bedrock model IDs — including `openai.gpt-oss-120b-1:0`, which does appear in `bedrock:ListFoundationModels` — are served by `bedrock-runtime` and return `404 ... does not exist` through Codex.

| `CODEX_MODEL` | `us-east-2` | `us-west-2` |
| --- | --- | --- |
| `openai.gpt-5.6-terra` (default) | yes | yes |
| `openai.gpt-5.6-luna` | yes | yes |
| `openai.gpt-5.6-sol` | yes | no |

To check a model in another region, ask the deployed agent — it already holds
credentials for the endpoint, so nothing extra needs to be exported locally:

```bash
python invoke.py "what model are you running as? reply with just the model id"
```

A model that is not served in `BEDROCK_REGION` fails the turn with
`404 ... does not exist`, visible in the response and in CloudWatch.

## Request and response format

Request:

```json
{ "prompt": "list the files in the workspace", "threadId": "optional-codex-thread-id" }
```

Response:

```json
{
  "response": "...Codex final response...",
  "threadId": "01JD...",
  "usage": { "input_tokens": 12, "cached_input_tokens": 0, "output_tokens": 34 }
}
```

`usage` includes `cached_input_tokens`, which is where the EFS-persisted thread pays off: resuming a long thread reuses cached prompt tokens instead of re-billing the full history.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEX_HOME` | `/mnt/efs/.codex` | Codex state: threads and `config.toml` |
| `WORKSPACE_DIR` | `/mnt/efs/workspace` | Git repo Codex reads and writes |
| `SKILLS_DIR` | `$CODEX_HOME/skills` | Shared skills; granted as a writable root |
| `CODEX_MODEL` | `openai.gpt-5.6-terra` | Bedrock model ID (GPT-5.6 family only) |
| `CODEX_REASONING_EFFORT` | `medium` | `minimal`, `low`, `medium`, `high`, or `xhigh` |
| `BEDROCK_REGION` | runtime region | Region for Bedrock inference; override to reach a model not served locally |
| `PORT` | `8080` | HTTP listen port |

## Notes for production

This is a tutorial sample. Before production use, consider:

- Codex runs with `approvalPolicy: "never"` and `sandboxMode: "workspace-write"`, so it edits files under `WORKSPACE_DIR` and `SKILLS_DIR` without asking. Scope the access point path accordingly, and keep `additionalDirectories` as narrow as the demo allows.
- The security group allows all egress and NFS from the whole VPC CIDR. Restrict both.
- There is no concurrency control. Two simultaneous turns against the same thread can interleave writes on EFS; add a lock if you invoke concurrently.
- Codex keeps some of its state in WAL-mode SQLite databases under `CODEX_HOME`. SQLite's WAL mode is not designed for concurrent access from multiple hosts over NFS. Thread resume relies on the `sessions/` rollout files rather than these databases, so the sample works, but do not assume the SQLite state is safe to share under real concurrency.
- Enable VPC Flow Logs and EFS backups.
