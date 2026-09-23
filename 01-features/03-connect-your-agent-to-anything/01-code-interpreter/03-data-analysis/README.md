# Advanced Data Analysis with AgentCore Code Interpreter

## Overview

This demo shows how to combine a **persistent file upload** with an **agent that reuses the same session**, enabling multi-step analysis of large datasets. The agent can load, transform, and query a 300K-row CSV file because the file was pre-loaded into the exact same sandbox session the agent's tool uses.

```
┌─────────────────────────────────────────────────────────────────────┐
│  data_analysis.py                                                   │
│                                                                     │
│  1. CodeInterpreter.start()        ← one session, held open        │
│  2. invoke("writeFiles", data.csv) ← 10MB CSV uploaded             │
│  3. agent("Perform EDA on data.csv")                               │
│          │                                                          │
│          └─▶ execute_python(code)  ← tool uses the SAME session    │
│                   │                                                 │
│                   ▼                                                 │
│            ┌──────────────────┐                                     │
│            │  Sandbox Session │                                     │
│            │  /data.csv  ✓   │  ← file is visible here            │
│            │  pandas loaded   │                                     │
│            └──────────────────┘                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Architecture

![Code Interpreter Architecture](../images/code-interpreter.png)

The sandbox enables agents to safely process queries by creating an isolated environment with a code interpreter, shell, and file system. After a Large Language Model helps with tool selection, code is executed within the session, before being returned to the user or agent for synthesis.

## How It Works

### The Key Pattern: Binding an Agent to an Existing Session

When you use `code_session()` (the context manager), each tool call creates and destroys its own independent session. Any files uploaded in a separate session will **not** be visible.

To give the agent access to a pre-uploaded file, you must create a local `execute_python` tool that closes over your open `CodeInterpreter` client:

```python
from strands import Agent, tool
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

# Step 1: Start a session and upload the file
code_client = CodeInterpreter(REGION)
code_client.start(session_timeout_seconds=1200)
code_client.invoke("writeFiles", {"content": [{"path": "data.csv", "text": csv_text}]})

# Step 2: Define a tool that uses THIS session
def make_session_agent(client: CodeInterpreter) -> Agent:
    @tool
    def execute_python(code: str, description: str = "") -> str:
        """Execute Python code in the AgentCore Code Interpreter sandbox."""
        response = client.invoke("executeCode", {
            "code": code,
            "language": "python",
            "clearContext": False,
        })
        for event in response["stream"]:
            return json.dumps(event["result"])

    return Agent(
        model=BedrockModel(model_id=MODEL_ID),
        tools=[execute_python],
        system_prompt=SYSTEM_PROMPT,
    )

# Step 3: Agent can now see the uploaded file
agent = make_session_agent(code_client)
agent("Load data.csv and report row count and column statistics")
```

This pattern is the key difference between this demo and `02-code-execution`: the tool is dynamically created to capture the specific client instance, rather than opening a new session per call.

### Session Timeout for Large Datasets

For multi-step analysis on large datasets, use a longer session timeout:

```python
code_client.start(session_timeout_seconds=1200)  # 20 minutes
```

The default timeout is 900 seconds (15 minutes). Large uploads (10MB CSV) and multi-step pandas operations can take several minutes.

### Exploratory Data Analysis Prompts

The demo runs two sequential queries against the same session:

**Query 1 — EDA overview:**
```
"Load the file 'data.csv' from the sandbox and perform exploratory data analysis.
Report row count, column stats, top values, and any notable distributions."
```

**Query 2 — Specific query:**
```
"Within 'data.csv', how many individuals with the first name 'Kimberly'
have 'Crocodile' as their favourite animal?"
```

Both queries run in the same session, so pandas and the loaded DataFrame are available without reloading.

### Sample Dataset

`samples/data.csv` contains ~300,000 synthetic records:

| Column | Type | Unique Values | Example |
|:-------|:-----|:-------------|:--------|
| `Name` | String | 1,722 | "Lisa White" |
| `Preferred_City` | String | 55 | "Prague" |
| `Preferred_Animal` | String | 50 | "Goat" |
| `Preferred_Thing` | String | 51 | "Pencil" |

## Prerequisites

```bash
pip install -r ../requirements.txt
```

Requires access to Claude Haiku 4.5 in `us-west-2` (or the region set in `AWS_DEFAULT_REGION`).

The file `samples/data.csv` must exist. It is approximately 10MB and contains 299,130 rows.

## Usage

```bash
# Run built-in EDA + detail queries
python data_analysis.py

# Run a single custom query
python data_analysis.py --query "How many rows have 'Goat' as Preferred_Animal?"
python data_analysis.py --query "What are the top 5 most common first names?"
```

## IAM Permissions

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
    "bedrock-agentcore:StopCodeInterpreterSession",
    "bedrock:InvokeModel"
  ],
  "Resource": "*"
}
```

## Files

| File | Description |
|:-----|:------------|
| `data_analysis.py` | Main demo script |
| `samples/data.csv` | 300K-row synthetic dataset |
| `../utils/code_interpreter_agent.py` | Shared agent constants (`REGION`, `MODEL_ID`, `SYSTEM_PROMPT`) |
