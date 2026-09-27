# Codex CLI  on AgentCore Runtime with S3 Files

Deploys Codex CLI  as an HTTP agent on AWS Bedrock AgentCore Runtime, with an S3 Files file system mounted at `/mnt/s3files` for persistent storage shared across sessions.

## Architecture

```
  ┌─────────────────────────┐         ┌─────────────────────────┐
  │  AgentCore Runtime      │         │  AgentCore Runtime      │
  │  Session A              │         │  Session B              │
  │  (Codex CLI)            │         │  (Codex CLI)            │
  │                         │         │                         │
  │  /mnt/s3files ──────────┼────┐    │  /mnt/s3files ──────────┼────┐
  └──────────┬──────────────┘    │    └──────────┬──────────────┘    │
             │                   │               │                   │
             │ Fetch API key     │               │ Fetch API key     │
             ▼                   ▼               ▼                   ▼
  ┌──────────────────────┐  ┌────────────────────────────────────────┐
  │  AWS Secrets Manager │  │  S3 Files File System                  │
  │  openai/codex        │  │                                        │
  │  {"api_key":"sk-…"}  │  │  ┌────────────────────────┐            │
  └──────────────────────┘  │  │  S3 Files Access Point │            │
                            │  │  (uid/gid 1000)        │            │
                            │  └───────────┬────────────┘            │
                            └──────────────┼─────────────────────────┘
                                           │
                                           ▼
                            ┌──────────────────────────────┐
                            │  S3 Bucket                   │
                            │  (agentcore-<account-id>)    │
                            │                              │
                            │  agents/                     │
                            │  ├── scripts/                │
                            │  ├── results/                │
                            │  └── ...                     │
                            └──────────────────────────────┘
```

Multiple runtime sessions mount the same S3 Files file system, enabling agents to share scripts, results, and data across independent invocations.

```
CloudFormation stack (cfn-vpc.yaml):
  VPC, subnets, NAT Gateway, Security Group
  S3 Files IAM role, file system, access point, mount targets

deploy.py creates:
  IAM execution role
  AgentCore Runtime (container from ECR, S3 Files mounted at /mnt/s3files)
```

## Prerequisites

### Python environment

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install boto3 awscli --force-reinstall --no-cache-dir

# Install Docker for Amazon Linux 2023 as example                                                                                                                                                                                                 
sudo dnf install -y docker                                                                                                                                                                     
# Start and enable Docker service                                                                                          
sudo systemctl start docker                                                                                                                                                                                         
sudo systemctl enable docker                                                                                               

# Add your user to the docker group                                                                                                                                                                                 
sudo usermod -aG docker $USER
# Apply group change without logging out                                                                                   
newgrp docker
                                                                                                                                                                                            
#Verify Docker works
docker --version                                                                                                                                                                                                                      
#Set up buildx for multi-platform builds                                                                                                                                                       
docker buildx create --use
```



### S3 Files IAM policies

The CloudFormation stack creates an IAM role (`S3FilesRole`) with the permissions required by S3 Files (S3, KMS, and EventBridge). For the full list of required policies, see the [S3 Files prerequisite policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-files-prereq-policies.html) documentation.

### OpenAI API Key Setup

**IMPORTANT**: Before deploying, you must create an AWS Secrets Manager secret containing your OpenAI API key. The container will fetch this secret at startup.

1. Obtain an OpenAI API key from [platform.openai.com/api-keys](https://platform.openai.com/api-keys)

2. Create the secret in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
    --name "openai/codex" \
    --description "OpenAI API key for Codex CLI" \
    --secret-string '{"api_key":"sk-YOUR-OPENAI-KEY-HERE"}' \
    --region us-west-2
```

Replace `sk-YOUR-OPENAI-KEY-HERE` with your actual OpenAI API key, and adjust the region to match your deployment region.

To update an existing secret:

```bash
aws secretsmanager update-secret \
    --secret-id "openai/codex" \
    --secret-string '{"api_key":"sk-YOUR-NEW-KEY-HERE"}' \
    --region us-west-2
```

To verify the secret was created:

```bash
aws secretsmanager describe-secret \
    --secret-id "openai/codex" \
    --region us-west-2
```

## Step-by-step guide

### Step 1 — Infrastructure setup (CloudFormation)

Run the setup script to create the S3 bucket, deploy the CloudFormation stack (VPC, subnets, NAT Gateway, Security Group, S3 Files), build the arm64 Docker image, and push it to ECR.

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

Send a prompt to the deployed agent. The first call creates a new session; subsequent calls can reuse the session ID and Codex thread ID for conversation continuity.

**Session A** — create a shared resource on the persistent filesystem:

```bash
python invoke.py "Write a file at /mnt/s3files/scripts/python_review.md with a Python code review checklist. Use the shell to create the directory and write the file directly."
```

Continue the conversation within the same session:

```bash
python invoke.py --session <session-id> --codex-session <codex-thread-id> "can you review python code, what standards are you going to use?"
```

View the standards defined in the persistent file:

```bash
python invoke.py --session <session-id> --codex-session <codex-thread-id> "what is inside /mnt/s3files/scripts/python_review.md, read it"
```

**Session B** — a completely new session accesses the same filesystem:

```bash
# New session automatically sees the same /mnt/s3files content
python invoke.py "list the files in /mnt/s3files/scripts/ and show me the python_review.md checklist"
```

Both sessions share `/mnt/s3files`, so anything written by one session is immediately available to others.

### Step 4 — Execute a command on the running session

Run a shell command directly on the container using the session ID from the previous step:

```bash
python exec_cmd.py --session 7fd93a80-8838-4721-abea-b1787dd0172c "ls -l /mnt/s3files"
```

### Step 5 — Cleanup

Delete all AgentCore resources (runtime, IAM role) and the CloudFormation stack. The S3 bucket is kept.

```bash
python cleanup.py
```

Or use the shell wrapper:

```bash
./cleanup.sh
```
