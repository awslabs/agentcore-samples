# AgentCore Code Interpreter

## Overview

[Amazon Bedrock AgentCore Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-overview.html) is a fully managed, sandboxed Python execution environment that your agents can use at runtime. The sandbox includes a Python 3.12 interpreter, a writable file system, a shell, and the AWS CLI — all isolated per session.

![AgentCore code interpreter](images/code-interpreter.png)


## How Code Interpreter Works

### Control Plane vs Data Plane

Code Interpreter uses two boto3 service clients:

| Client | Boto3 Service | Purpose |
|:-------|:-------------|:--------|
| Control plane | `bedrock-agentcore-control` | Create and delete Code Interpreter resources |
| Data plane | `bedrock-agentcore` | Start/stop sessions and invoke tools |

For most use cases, you don't need to interact with the control plane at all — the `bedrock-agentcore` SDK's `CodeInterpreter` class and `code_session()` context manager handle resource creation automatically using a shared default interpreter.

### Session Lifecycle

```
CodeInterpreter(region)           # Attach to the default shared interpreter
    │
    ├── .start()                  # Allocate a new sandbox session
    │       └── sessionId         # Unique token for this session
    │
    ├── .invoke("executeCode", …) # Run code in that session
    ├── .invoke("writeFiles", …)  # Upload files
    ├── .invoke("executeCommand",…)# Run shell commands
    │
    └── .stop()                   # Terminate and release the session
```

Files written during a session persist until the session ends. Each tool call within a session shares the same filesystem and Python kernel state.

### The `code_session()` Context Manager

The simplest way to use the code interpreter is the `code_session()` context manager:

```python
from bedrock_agentcore.tools.code_interpreter_client import code_session

with code_session("us-west-2") as client:
    response = client.invoke("executeCode", {
        "code": "print(2 + 2)",
        "language": "python",
        "clearContext": False,
    })
    for event in response["stream"]:
        result = event["result"]
        print(result["structuredContent"]["stdout"])  # "4"
```

The context manager automatically starts a session on enter and stops it on exit.

### Available Tools

| Tool | Input | Output |
|:-----|:------|:-------|
| `executeCode` | `code` (str), `language` (`"python"`), `clearContext` (bool) | `stdout`, `stderr`, `exitCode`, execution time |
| `executeCommand` | `command` (str) | `stdout`, `stderr`, `exitCode` |
| `writeFiles` | `content` (list of `{path, text}`) | confirmation message |
| `listFiles` | `path` (str) | list of `{name, description}` entries |

### Response Structure

Every tool call returns a streaming response. Iterate over it to get the result:

```python
response = client.invoke("executeCode", {"code": "x = 42\nprint(x)", "language": "python"})
for event in response["stream"]:
    result = event["result"]
    # result keys:
    #   isError         (bool)  — True if execution failed
    #   content         (list)  — [{type, text}] human-readable summary
    #   structuredContent       — machine-readable details
    #     .stdout       (str)   — captured stdout
    #     .stderr       (str)   — captured stderr
    #     .exitCode     (int)   — process exit code
    #     .executionTime(float) — wall-clock seconds
```

### Custom Interpreter Resources

By default, `CodeInterpreter` uses a shared AgentCore-managed interpreter. For advanced use cases — such as running AWS CLI commands that need specific IAM permissions — you create a **custom Code Interpreter resource** with your own execution role:

```python
cp_client = boto3.client("bedrock-agentcore-control", region_name=region)

resp = cp_client.create_code_interpreter(
    name="my-interpreter",
    executionRoleArn="arn:aws:iam::123456789012:role/my-code-interp-role",
    networkConfiguration={"networkMode": "PUBLIC"},
)
interpreter_id = resp["codeInterpreterId"]

# Start a session on the custom interpreter
dp_client = boto3.client("bedrock-agentcore", region_name=region)
sess = dp_client.start_code_interpreter_session(
    codeInterpreterIdentifier=interpreter_id,
    sessionTimeoutSeconds=900,
)
session_id = sess["sessionId"]
```

The execution role must trust `bedrock-agentcore.amazonaws.com`. Any IAM permissions you attach to the role are available inside the sandbox (e.g., `AmazonS3FullAccess` lets `aws s3` CLI commands work).

## IAM Permissions

### Caller Permissions (your local credentials or agent)

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreateCodeInterpreter",
    "bedrock-agentcore:GetCodeInterpreter",
    "bedrock-agentcore:ListCodeInterpreters",
    "bedrock-agentcore:DeleteCodeInterpreter",
    "bedrock-agentcore:StartCodeInterpreterSession",
    "bedrock-agentcore:InvokeCodeInterpreter",
    "bedrock-agentcore:StopCodeInterpreterSession"
  ],
  "Resource": "*"
}
```

For model invocation (Strands agent demos), also add:
```json
{ "Effect": "Allow", "Action": "bedrock:InvokeModel", "Resource": "*" }
```

See the [Code Interpreter IAM reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-permissions.html) for full details.

### Execution Role (for custom interpreter resources only)

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

Attach additional policies (e.g., `AmazonS3FullAccess`) to grant the sandbox those permissions.

## Tutorials

| Folder | Approach | What You'll Learn |
|:-------|:---------|:------------------|
| [01-file-operations/](01-file-operations/) | Direct SDK API | Upload files, list the sandbox filesystem, execute a script |
| [02-code-execution/](02-code-execution/) | Strands agent | Agent generates and verifies code answers in a sandbox |
| [03-data-analysis/](03-data-analysis/) | Strands agent | Multi-step EDA on a 300K-row CSV with a persistent session |
| [04-run-commands/](04-run-commands/) | Direct boto3 API | Shell commands, pip install, and AWS CLI from inside the sandbox |

Demos 02 and 03 share a single Strands agent defined in `utils/code_interpreter_agent.py`. Demos 01 and 04 use the SDK directly for full control over session lifecycle.

## Quick Start

```bash
pip install -r requirements.txt

# No agent framework — direct SDK
python 01-file-operations/file_operations.py

# Strands agent answering questions with code
python 02-code-execution/code_execution.py
python 02-code-execution/code_execution.py --query "List all prime numbers under 50"

# Strands agent performing data analysis
python 03-data-analysis/data_analysis.py
python 03-data-analysis/data_analysis.py --query "How many unique cities are in data.csv?"

# Shell and AWS CLI commands (requires execution role)
export EXECUTION_ROLE_ARN=arn:aws:iam::<account>:role/<role-name>
python 04-run-commands/run_commands.py
python 04-run-commands/run_commands.py --skip-s3
```

## Files

| File | Description |
|:-----|:------------|
| `requirements.txt` | Python dependencies for all sub-demos |
| `utils/code_interpreter_agent.py` | Shared Strands agent with `execute_python` tool |
| `01-file-operations/file_operations.py` | File upload, listing, and execution demo |
| `02-code-execution/code_execution.py` | Agent-based code execution demo |
| `03-data-analysis/data_analysis.py` | Advanced data analysis demo |
| `04-run-commands/run_commands.py` | Shell and AWS CLI commands demo |
