# Claude Code on AgentCore Runtime with EFS

Deploys Claude Code as an HTTP agent on AWS Bedrock AgentCore Runtime, with an EFS file system mounted at `/mnt/efs` for persistent storage shared across sessions.

## Architecture

```
  ┌─────────────────────────┐         ┌─────────────────────────┐
  │  AgentCore Runtime      │         │  AgentCore Runtime      │
  │  Session A              │         │  Session B              │
  │  (Claude Code)          │         │  (Claude Code)          │
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
                    │  │   root /shared)        │                      │
                    │  └────────────────────────┘                      │
                    └──────────────────────────────────────────────────┘
```

Multiple runtime sessions mount the same EFS file system, enabling agents to share skills, results, and data across independent invocations.

```
CloudFormation stack (cfn-vpc.yaml):
  VPC, subnets, NAT Gateway, Security Group
  EFS file system, access point, mount targets

deploy.py creates:
  IAM execution role
  AgentCore Runtime (container from ECR, EFS mounted at /mnt/efs)
```

## Prerequisites

### Python environment

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

If you need to update an existing runtime (e.g. after rebuilding the Docker image), run:

```bash
python update.py
```

### Step 3 — Invoke the agent

Send a prompt to the deployed agent. The first call creates a new session; subsequent calls can reuse the session ID for conversation continuity.

**Session A** — create a shared skill on the persistent filesystem:

```bash
python invoke.py "can u create a new skill, to review python code? This skill should be created into /mnt/efs/skills/"
```

Continue the conversation within the same session:

```bash
python invoke.py --session <session-a-id> "now add unit tests for that skill"
```

**Session B** — a completely new session accesses the same filesystem and uses the skill created by Session A:

```bash
python invoke.py "list the skills available in /mnt/efs/skills/ and use the python review skill to review this code: def add(a,b): return a+b"
```

Both sessions share `/mnt/efs`, so anything written by one session is immediately available to others.

### Step 4 — Execute a command on the running session

Run a shell command directly on the container using the session ID from the previous step:

```bash
python exec_cmd.py --session <session-id> "ls -l /mnt/efs"
```

### Step 5 — Cleanup

Delete all AgentCore resources (runtime, IAM role) and the CloudFormation stack.

```bash
python cleanup.py
```

Or use the shell wrapper:

```bash
./cleanup.sh
```
