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
# When done, deregister it:
app.complete_async_task(task_id)
```

These two calls drive the runtime's **health signal only**: while a task is
registered, `GET /ping` answers `HealthyBusy` instead of `Healthy`, which is how
the runtime knows the microVM is still working and should not be reclaimed.

The SDK stores no task result and offers no lookup by task ID, so an agent that
lets the caller retrieve a result later has to keep its own registry. See
`01_weekly_report_generator_async` for that pattern. Two consequences to design
around:

1. The registry lives in the memory of the microVM serving the session, so the
   caller must send the **same `runtimeSessionId`** on the status call that it
   used to submit the work. A new session ID lands on a different microVM.
2. That state is lost when the session is recycled. Persist it (DynamoDB, S3)
   for anything beyond a demo.

This lets you build agents that:

1. Accept a request and immediately return a task ID
2. Run expensive computation in a background thread
3. Report completion or failure when the work finishes
4. Let the caller retrieve results later, on the same session

### Key API Methods

| Method | Description |
|:-------|:------------|
| `app.add_async_task(name, metadata=None)` | Register a background task, returns an `int` task ID |
| `app.complete_async_task(task_id)` | Deregister the task, returning `True` if it was known |
| `app.get_async_task_info()` | Active task count plus the name and duration of each running task |

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
