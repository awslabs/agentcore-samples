# fuse example

A per-user-isolation example for Amazon Bedrock AgentCore Runtime. Each
session mounts its own `sessions/<sessionId>/` prefix of a shared S3
bucket as a local directory **over FUSE** (Mountpoint for Amazon S3)
instead of through direct `boto3` calls, and the agent itself **is the
[Strands harness](https://strandsagents.com/docs/user-guide/harness/quickstart/)**
(`@strands-agents/cli`) -- a coding-agent CLI shaped like Anthropic's
`claude` CLI -- spawned as a subprocess, with its own built-in shell/file
tools writing and reading through that mount. Same architecture as
awslabs/agentcore-samples'
[`01-claude-code-with-s3-files`](https://github.com/awslabs/agentcore-samples/tree/main/01-features/02-host-your-agent/01-runtime/04-coding-agents/01-claude-code-with-s3-files),
with `strands` standing in for `claude` and a FUSE mount standing in for
that example's S3 Files access point.

The isolation itself -- what actually stops session A from reading
session B's files -- comes entirely from IAM, via **Session Context
Keys**. The next two sections cover what those are in general, then how
AgentCore Runtime uses them.

## What are context keys?

An IAM policy's `Condition` block compares values from the actual
request against values you write into the policy; those request-time
values are **condition keys**. A *global* condition key (prefixed
`aws:`) is populated by AWS itself for most requests, regardless of
service -- `aws:SourceIp` (caller's IP), `aws:PrincipalOrgID` (caller's
AWS Organization), `aws:username` (IAM user name). A policy can
reference a condition key's value directly inside a `Resource` or
`Condition` element as `${aws:<key-name>}`, so one statement can grant
access scoped to *whatever the actual caller turns out to be*, instead
of hardcoding every possible value. See AWS's
[condition keys reference](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html)
for the full list and how availability varies by request type.

## Using context keys with AgentCore Runtime

AgentCore Runtime stamps two context keys onto the STS session behind
every execution-role credential it hands an agent:

- `aws:AgentCoreRuntimeSourceSid` -- the current session's id
  (`runtimeSessionId`).
- `aws:AgentCoreRuntimeSourceArn` -- the ARN of the runtime handling
  that session.

Because these are ordinary global condition keys, `${aws:...}` works
with them exactly like it does with `aws:username` or
`aws:PrincipalOrgID`. That means one IAM statement, written once at
deploy time, scopes every session to its own slice of a shared
resource -- no per-session IAM policy, no application-level auth check:

```json
{ "Effect": "Allow", "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::my-bucket/sessions/${aws:AgentCoreRuntimeSourceSid}/*" }
```

Whichever session calls through this role, IAM substitutes that
session's own id before evaluating the policy -- session A's
credentials only ever resolve to `sessions/<A's id>/*`, session B's only
to `sessions/<B's id>/*`. `infra/common.sh`'s `agent_permissions_policy()`
and `infra/deploy.sh`'s bucket policy both apply exactly this pattern:
one on the execution role (identity-based), one on the bucket
(resource-based, also pinned to this runtime's ARN via
`aws:AgentCoreRuntimeSourceArn`) as a second, defense-in-depth layer.

## Architecture

```
 Caller: session_id = session_id_for(username)   -- pure function, nothing provisioned
   |
   v
 InvokeAgentRuntime(runtimeSessionId) --> AgentCore Runtime (USER 0 microVM)
                                                          |
                                               server.js (plain Node http server)
                                                          |
                                     STS stamps aws:AgentCoreRuntimeSourceSid/Arn
                                                          |
                                      mount-s3 --prefix sessions/<sid>/ bucket /mnt/s3
                                                          |
                                     (mount succeeds only if ListBucket is allowed)
                                                          |
                              strands -p "..." --session-id <sid> --effort off
                                  (Strands harness CLI, built-in shell+file tools)
                                                          |
                                             writes /mnt/s3/note-from-<sid>.txt
                                                          |
                                     every read/write is a real GetObject/PutObject,
                                       signed by the execution role, evaluated against
                                            the bucket's own policy (session + runtime
                                                         pinned via ${aws:...})

 exec-cmd.sh --------> ExecuteCommand / InvokeAgentRuntimeCommandShell --------> same
   (raw shell or interactive PTY, bypassing server.js/strands entirely)          running
                                                                                  container
```

## Before you run this

- S3 bucket-policy changes take ~30s to propagate. A `DENIED` right
  after `deploy.sh` isn't necessarily broken -- wait and retry.
- **Bedrock model access**: the Strands harness calls `FUSE_MODEL_ID`
  (default `us.anthropic.claude-haiku-5-5`) via Bedrock. Confirm it's
  invokable first:
  ```bash
  aws bedrock-runtime invoke-model --region "$AWS_REGION" --model-id us.anthropic.claude-haiku-5-5 \
    --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
    --cli-binary-format raw-in-base64-out /tmp/out.json && cat /tmp/out.json
  ```
  If that fails, grant model access in the Bedrock console, or override
  `FUSE_MODEL_ID`. `--effort off` (always passed by `server.js`) is
  required by some models, including Haiku.
- **Root in the container is expected.** `agent/Dockerfile` declares
  `USER 0` because `mount(2)` is privileged; that root never leaves this
  agent's own dedicated, per-session microVM.
- Mountpoint for S3 is write-once-sequential, not a real filesystem:
  `>>` append fails with `Operation not permitted`. To edit a file,
  rewrite it whole (read, modify, write from a temp file, `cp` back).

## Layout

```
agent/
  Dockerfile    node:22-slim, USER 0, bakes mount-s3 + @strands-agents/cli
  server.js     plain Node http server (no AgentCore SDK) -- mounts, then spawns `strands -p ...`
infra/
  common.sh           config, shared helpers, session_id_for()
  agentcore_boto3.py  boto3 wrapper for AgentCore CP+DP calls (invoke, exec)
  deploy.sh           infra: image -> role -> bucket -> runtime -> bucket policy
  test.sh             execution: both users' deny+mount+write round trip, with a summary
  exec-cmd.sh         execution: raw shell command, or --shell for a real interactive PTY
  shell_session.py    InvokeAgentRuntimeCommandShell client (raw stdin/stdout + resize relay)
  cleanup.sh          deletes everything deploy.sh created (dry-run by default)
```

No DynamoDB table, no Lambda, no provisioning step at all: the mount
prefix *is* the session id, and the session id is a pure function of the
username (`session_id_for()` in `common.sh`).

## Running it

```bash
cd fuse
python3 -m venv .venv && .venv/bin/pip install -r infra/requirements.txt

cd infra
./deploy.sh
./test.sh
```

State (bucket/runtime names) is recorded in
`/tmp/fuse-per-user-isolation-state` (override with `FUSE_STATE_FILE`).

`test.sh` makes two calls per user on the same session (a session mounts
at most once for its life, so the deny attempt has to go first):

1. Try to mount the **other** user's prefix -- expect
   `{"mount": "DENIED", ...}`.
2. Mount its **own** prefix -- expect `{"mount": "mounted",
   "note_content": "...session id..."}`, where `note_content` is read
   back independently by `server.js`, not just trusted from the
   harness's own claim.

### Execute a command, or open a shell

```bash
./exec-cmd.sh alice 'ls -la /mnt/s3'   # one-shot, via ExecuteCommand
./exec-cmd.sh alice --shell            # real interactive PTY, via InvokeAgentRuntimeCommandShell
```

Both need the session to have been invoked recently (`./test.sh` or an
invoke first) for the mount to be live. `--shell` relays this terminal's
raw stdin/stdout and resize events to the remote PTY; reconnecting with
the same `--shell-id` resumes the same shell. `InvokeAgentRuntimeCommandShell`
isn't a boto3-modeled operation, so `shell_session.py` talks to it
through the `bedrock-agentcore` SDK directly instead.

### Testing the agent itself

`./test.sh` exercises the mount/IAM isolation with a fixed prompt. To
iterate on the Strands harness directly -- different prompts, without a
round trip through `InvokeAgentRuntime` for each one -- open a shell on
a session that already has its mount live and run `strands` yourself:

```bash
./test.sh                  # gets alice's session running + mounted
./exec-cmd.sh alice --shell
```

Same flags `server.js` itself passes, just against the real mount
instead of the hardcoded note-writing prompt. Both extra flags matter,
not just `-p`/`--model`:

- `--effort off` -- without it, Haiku (and some other models) reject the
  harness's default extended-thinking config outright
  (`"thinking.type.enabled" is not supported for this model`).
- `--memory off` -- the harness's long-term memory feature calls a
  second, hardcoded Bedrock model in the background regardless of
  `--model`. The execution role's IAM policy is scoped to the one
  configured model (see `common.sh`'s `agent_permissions_policy()`), so
  that background call comes back `AccessDenied` ("memory extraction
  failed") unless memory is turned off.

### Cleanup

```bash
./cleanup.sh                    # dry run
./cleanup.sh --yes              # deletes the runtime, bucket, and the agent's execution role
./cleanup.sh --yes --purge-ecr  # also deletes the ECR repo + image
```

## A note on session IDs

A real hazard with this pattern: IAM's `*` matches across `/`, so a
session id like `team` would also grant `team/alice`'s prefix, and `..`
resolves as a relative path segment and escapes the session folder
entirely. This example's session ids (`session_id_for()` in
`infra/common.sh`) are `user-<username>-<64-hex-char sha256 digest>` --
no slash, no `.`/`..` -- so this risk doesn't apply here, but it's why
the session id is always derived from the username rather than accepted
as a literal string from a caller.
