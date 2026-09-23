# Execute Commands in runtime Sessions

## Overview

The `invoke_agent_runtime_command` API lets you run shell commands directly inside an AgentCore runtime session's microVM. This is useful for debugging, inspecting the environment, installing packages, or running scripts alongside your agent.

## How It Works

1. **Create a session** — invoke the agent once to start a microVM
2. **Run commands** — use `invoke_agent_runtime_command` to execute shell commands in that session
3. **Stream output** — stdout and stderr are streamed back as events in real time

```python
client = boto3.client('bedrock-agentcore')

# Execute a command in an existing session
response = client.invoke_agent_runtime_command(
    agentRuntimeArn=arn,
    runtimeSessionId=session_id,  # must be an active session
    body={
        'command': 'ls -la /app',  # shell command to run
        'timeout': 60,             # max seconds (default: 300, max: 3600)
    },
)
```

### Event Stream Format

The response is a stream of events:

```python
for event in response['stream']:
    if 'chunk' in event:
        chunk = event['chunk']

        if 'contentStart' in chunk:
            # Command execution started
            pass

        if 'contentDelta' in chunk:
            delta = chunk['contentDelta']
            if 'stdout' in delta:
                print(delta['stdout'], end='')    # standard output
            if 'stderr' in delta:
                print(delta['stderr'], end='')    # standard error

        if 'contentStop' in chunk:
            exit_code = chunk['contentStop']['exitCode']  # 0 = success
            status = chunk['contentStop']['status']       # COMPLETED or TIMED_OUT
```

### `invoke_agent_runtime_command` Parameters

| Parameter | Required | Description |
|:----------|:---------|:------------|
| `agentRuntimeArn` | Yes | ARN of the runtime |
| `runtimeSessionId` | Yes | Must be an active session (invoke the agent first to create one) |
| `body.command` | Yes | Shell command to execute |
| `body.timeout` | No | Max seconds to wait (default: 300, min: 1, max: 3600) |

## Use Cases

- **Debugging** — inspect files, environment variables, running processes
- **Package management** — `pip install` additional packages at runtime
- **File operations** — create, read, or modify files in the session
- **System inspection** — check available resources, network connectivity
- **Running scripts** — execute Python or shell scripts alongside your agent

## What This Demo Shows

The `invoke.py` script:
1. Invokes the agent to create a session (starts the microVM)
2. Runs several commands: `ls`, `python3 --version`, `pip list`, `df -h`, `ps aux`
3. Runs inline Python to inspect the environment
4. Writes and reads a file
5. Stops the session

## Files

| File | Description |
|:-----|:------------|
| `agent.py` | Simple agent (needed to create a session) |
| `requirements.txt` | Dependencies |
| `deploy.py` | Deploys the agent |
| `invoke.py` | Creates a session, runs shell commands, streams output |
| `cleanup.py` | Deletes runtime, endpoint, S3 artifact, IAM role |

## Quick Start

```bash
python deploy.py     # Deploy the agent
python invoke.py     # Run commands inside the session
python cleanup.py    # Clean up
```
