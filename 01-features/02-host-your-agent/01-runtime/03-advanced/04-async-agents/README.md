# Async Agents

## Overview

Asynchronous agents handle long-running tasks in the background while remaining responsive to the user. Instead of blocking until a task completes, the agent starts the work, returns a task ID, and lets the user continue the conversation. The user can check status or retrieve results later.

This pattern is essential for agents that perform compute-intensive operations like data analysis, report generation, or multi-step workflows that take minutes rather than seconds.

## How Async Works in AgentCore runtime

AgentCore runtime provides built-in async task management through the `BedrockAgentCoreApp` class:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# Register a new async task — returns a task ID
task_id = app.add_async_task("my_analysis")

# Run the work in a background thread...
# When done, mark it complete:
app.complete_async_task(task_id)

# Or if it fails:
app.fail_async_task(task_id)
```

The client can query task status through the runtime's task management API. This lets you build agents that:

1. Accept a request and immediately return a task ID
2. Run expensive computation in a background thread
3. Report completion or failure when the work finishes
4. Let the user retrieve results at any time

### Key API Methods

| Method | Description |
|:-------|:------------|
| `app.add_async_task(name)` | Register a new background task, returns task ID |
| `app.complete_async_task(task_id)` | Mark a task as successfully completed |
| `app.fail_async_task(task_id)` | Mark a task as failed |

## Examples

This folder contains two async agent examples.

### 01 — Weekly Report Generator

A multi-tool agent that collects data from multiple sources (team updates, meeting notes, metrics, bug trackers), performs analysis, generates visualizations with matplotlib, and uploads comprehensive weekly reports to S3.

**Key features:**
- 16 different tools orchestrated by a single agent
- Sentiment analysis and risk scoring
- ML-based forecasting with scikit-learn
- S3 integration for report storage

**Deploy and run:**
```bash
cd 01_weekly_report_generator_async
python deploy.py    # Deploy the agent to AgentCore runtime
python invoke.py    # Invoke the agent
python cleanup.py   # Clean up all resources
```

### 02 — Async Data Analysis

A conversational agent that delegates data analysis tasks to a background coding agent. The coding agent generates Python code, executes it in AgentCore Code Interpreter, and saves results to S3 — all while the primary agent stays responsive.

**Key features:**
- Multi-agent architecture (orchestrator + coding agent)
- Code Interpreter integration for secure code execution
- Bedrock Guardrails for input/output validation
- Thread pool with retry logic for reliability

**Deploy and run:**
```bash
cd 02_async_data_analysis
python deploy.py    # Deploy the agent to AgentCore runtime
python invoke.py    # Invoke the agent
python cleanup.py   # Clean up all resources
```

## Prerequisites

- AWS account with access to Amazon Bedrock AgentCore
- Python 3.12+
- AWS CLI configured with appropriate credentials
