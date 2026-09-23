# Shell and AWS CLI Commands via AgentCore Code Interpreter

## Overview

The AgentCore Code Interpreter sandbox includes a full shell environment and the AWS CLI — not just Python. This demo shows how to use the `executeCommand` tool to run arbitrary shell commands and interact with AWS services directly from inside the sandbox. Because the sandbox runs under a custom IAM execution role, it can make authenticated API calls without any credential management in your code.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Your Script (run_commands.py)                                      │
│                                                                     │
│  1. cp_client.create_code_interpreter(executionRoleArn=...)         │
│           │                                                         │
│           ▼                                                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Custom Code Interpreter Resource                           │   │
│  │  ┌─────────────────────────────────────────────────────┐   │   │
│  │  │  Sandbox Session                                    │   │   │
│  │  │                                                     │   │   │
│  │  │  executeCommand("echo Hello")     → stdout          │   │   │
│  │  │  executeCommand("pip install …")  → stdout/stderr   │   │   │
│  │  │  executeCommand("aws s3 cp …")    → AWS API call    │   │   │
│  │  │       │                                             │   │   │
│  │  │       └─ assumes executionRoleArn ─▶ S3:PutObject   │   │   │
│  │  └─────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Architecture

![Code Interpreter Architecture](../images/code-interpreter.png)

The sandbox enables agents to safely process queries by creating an isolated environment with a code interpreter, shell, and file system. After a Large Language Model helps with tool selection, code is executed within the session, before being returned to the user or agent for synthesis.

## How It Works

### Why a Custom Code Interpreter Resource?

The default shared Code Interpreter resource (`code_session()` or `CodeInterpreter()` without arguments) has no execution role and therefore no AWS credentials. To run `aws` CLI commands that call AWS APIs, you must create a **custom Code Interpreter resource** with an `executionRoleArn`. AgentCore assumes this role when the sandbox session starts, making its permissions available inside the sandbox.

```python
import boto3

cp_client = boto3.client("bedrock-agentcore-control", region_name=region)

resp = cp_client.create_code_interpreter(
    name="my-custom-interpreter",
    executionRoleArn="arn:aws:iam::123456789012:role/my-code-interp-role",
    networkConfiguration={"networkMode": "PUBLIC"},
)
interpreter_id = resp["codeInterpreterId"]
```

The execution role must have a trust relationship with `bedrock-agentcore.amazonaws.com`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "123456789012"}
    }
  }]
}
```

### Control Plane vs Data Plane

This demo uses both SDK planes directly (without the `CodeInterpreter` helper class):

| Operation | Client | API |
|:----------|:-------|:----|
| Create/delete interpreter resource | `bedrock-agentcore-control` | `create_code_interpreter`, `delete_code_interpreter` |
| Start/stop session | `bedrock-agentcore` | `start_code_interpreter_session`, `stop_code_interpreter_session` |
| Invoke tools | `bedrock-agentcore` | `invoke_code_interpreter` |

```python
dp_client = boto3.client("bedrock-agentcore", region_name=region)

# Start a session on your custom interpreter
sess = dp_client.start_code_interpreter_session(
    codeInterpreterIdentifier=interpreter_id,
    name="my-session",
    sessionTimeoutSeconds=900,
)
session_id = sess["sessionId"]
```

### Running Shell Commands with `executeCommand`

`executeCommand` runs any shell command available in the sandbox environment. The sandbox includes:

- `bash`, `sh`, standard Unix utilities
- `python3` (3.12), `pip`
- `aws` CLI (with credentials from the execution role)

```python
response = dp_client.invoke_code_interpreter(
    codeInterpreterIdentifier=interpreter_id,
    sessionId=session_id,
    name="executeCommand",
    arguments={"command": "echo 'Hello from AgentCore sandbox!'"},
)
for event in response["stream"]:
    result = event["result"]
    print(result["structuredContent"]["stdout"])
    print(result["structuredContent"]["stderr"])
    print(result["structuredContent"]["exitCode"])  # 0 = success
```

### Running AWS CLI Commands

Once the sandbox session is running under an IAM role, standard `aws` CLI commands work without any additional configuration:

```python
# Create an S3 bucket from inside the sandbox
call_tool(dp_client, interpreter_id, session_id, "executeCommand",
    {"command": f"aws s3 mb s3://my-bucket --region {region}"})

# Upload a file
call_tool(dp_client, interpreter_id, session_id, "executeCommand",
    {"command": "aws s3 cp stats.py s3://my-bucket/"})

# List bucket contents
call_tool(dp_client, interpreter_id, session_id, "executeCommand",
    {"command": "aws s3 ls s3://my-bucket/"})
```

The AWS CLI inside the sandbox uses the execution role's credentials automatically through the instance metadata service.

### Always Clean Up

Custom Code Interpreter resources persist until explicitly deleted. Always clean up in a `try/finally` block:

```python
try:
    # ... your work ...
finally:
    dp_client.stop_code_interpreter_session(
        codeInterpreterIdentifier=interpreter_id,
        sessionId=session_id,
    )
    cp_client.delete_code_interpreter(
        codeInterpreterId=interpreter_id,
    )
```

### Demo Walkthrough

The script runs these steps in sequence:

1. **Create a custom interpreter** with your execution role
2. **Start a session** on that interpreter
3. **Run shell commands**: `echo`, `python3 --version`, `pip install pandas`
4. **Upload a file** (`stats.py`) to the sandbox using `writeFiles`
5. **S3 operations**: create bucket → upload file → list contents (skippable with `--skip-s3`)
6. **Clean up**: stop session, delete interpreter, delete S3 bucket

## Prerequisites

```bash
pip install -r ../requirements.txt
```

Create an IAM execution role for the sandbox (one-time setup):

```bash
# Create a trust policy document
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "<your-account-id>"}
    }
  }]
}
EOF

# Create the role
aws iam create-role \
  --role-name agentcore-code-interpreter-role \
  --assume-role-policy-document file://trust-policy.json

# Attach S3 permissions (adjust as needed)
aws iam attach-role-policy \
  --role-name agentcore-code-interpreter-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess

export EXECUTION_ROLE_ARN=$(aws iam get-role \
  --role-name agentcore-code-interpreter-role \
  --query 'Role.Arn' --output text)
```

## Usage

```bash
# Full demo including S3 operations
python run_commands.py

# Skip S3 operations (still demos shell commands)
python run_commands.py --skip-s3
```

## IAM Permissions

**Caller (your local credentials or CI/CD role):**

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreateCodeInterpreter",
    "bedrock-agentcore:GetCodeInterpreter",
    "bedrock-agentcore:DeleteCodeInterpreter",
    "bedrock-agentcore:StartCodeInterpreterSession",
    "bedrock-agentcore:InvokeCodeInterpreter",
    "bedrock-agentcore:StopCodeInterpreterSession",
    "s3:CreateBucket",
    "s3:PutObject",
    "s3:GetObject",
    "s3:ListBucket",
    "s3:DeleteBucket",
    "s3:DeleteObject"
  ],
  "Resource": "*"
}
```

**Execution role (assumed by the sandbox at runtime):**

```json
{
  "Effect": "Allow",
  "Action": ["s3:*"],
  "Resource": "*"
}
```

Trust policy for the execution role:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "<your-account-id>"}
    }
  }]
}
```

See [Code Interpreter IAM reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-permissions.html) for full details.

## Files

| File | Description |
|:-----|:------------|
| `run_commands.py` | Main demo script |
| `samples/stats.py` | Sample Python script uploaded to the sandbox |
