# AgentCore Evaluation Utility

Python utility for extracting CloudWatch trace data and evaluating agent sessions using the AgentCore Evaluation DataPlane API.

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Configure AWS credentials with access to CloudWatch Logs and AgentCore Evaluation API:

```bash
aws configure
```

Or set environment variables:

```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"
```

## Usage

```python
from utils import EvaluationClient

# Initialize client
client = EvaluationClient(region="us-east-1")

# Evaluate a session
results = client.evaluate_session(
    session_id="your-session-id",
    evaluator_ids=["Builtin.Helpfulness"],
    agent_id="your-agent-id",
    region="us-east-1"
)

# Print results
for result in results.results:
    print(f"{result.evaluator_name}: {result.value} - {result.label}")
    print(f"Explanation: {result.explanation}")
```

## Multi-Evaluator Support

Evaluate with multiple evaluators in a single call:

```python
results = client.evaluate_session(
    session_id="session-id",
    evaluator_ids=["Builtin.Helpfulness", "Builtin.Accuracy", "Builtin.Harmfulness"],
    agent_id="agent-id",
    region="us-east-1"
)
```

## Auto-Save and Metadata

Save input/output files and track experiments:

```python
results = client.evaluate_session(
    session_id="session-id",
    evaluator_ids=["Builtin.Helpfulness"],
    agent_id="agent-id",
    region="us-east-1",
    auto_save_input=True,   # Saves to evaluation_input/
    auto_save_output=True,  # Saves to evaluation_output/
    auto_create_dashboard=True,  # generates data for HTML dashboard available locally
    metadata={. # pass literally anything
        "experiment": "baseline",
        "description": "Initial evaluation run"
    }
)
```

Input files contain only the spans sent to the API for exact replay. Output files contain complete results with metadata.

## Implementation Details

The utility queries CloudWatch Logs for OpenTelemetry spans and runtime logs, filters relevant data (gen_ai attributes and conversation logs), and submits to the evaluation API. Default lookback window is 7 days with a maximum of 1000 items per evaluation.
