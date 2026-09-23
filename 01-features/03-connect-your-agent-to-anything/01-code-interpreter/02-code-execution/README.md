# Agent-Based Code Execution with AgentCore Code Interpreter

## Overview

This demo shows how to give a Strands agent the ability to write and run Python code to verify its answers. The agent receives a natural language question, generates code to solve it, executes that code in an isolated AgentCore sandbox, and synthesises the result into a clear answer.

```
┌────────────┐   "What is the largest prime < 84?"   ┌──────────────────────┐
│   User     │ ────────────────────────────────────▶ │   Strands Agent      │
│            │◀──────────────────────────────────── │   (Claude Haiku)     │
│            │  "The answer is 83. Verified by code" │                      │
└────────────┘                                        │  @tool execute_python│
                                                      │       │              │
                                                      └───────┼──────────────┘
                                                              │ code_session()
                                                              ▼
                                                      ┌──────────────────────┐
                                                      │  Code Interpreter    │
                                                      │  Sandbox             │
                                                      │                      │
                                                      │  def is_prime(n):... │
                                                      │  primes = [...]      │
                                                      │  → stdout: "[..., 83]"│
                                                      └──────────────────────┘
```

## Architecture

![Code Interpreter Architecture](../images/code-interpreter.png)

The sandbox enables agents to safely process queries by creating an isolated environment with a code interpreter, shell, and file system. After a Large Language Model helps with tool selection, code is executed within the session, before being returned to the user or agent for synthesis.

## Framework Variants

This sample is available for two agentic frameworks:

| Variant | Framework | Model |
|:--------|:----------|:------|
| `code_execution.py` | Strands Agents | Claude Haiku 4.5 (`global.anthropic.claude-haiku-4-5-20251001-v1:0`) |
| LangChain variant | LangChain (`create_tool_calling_agent` + `AgentExecutor`) | Claude Haiku 4.5 (`global.anthropic.claude-haiku-4-5-20251001-v1:0`) |

Both variants define the same `execute_python` tool with `@tool` decorator and use the same system prompt instructing the agent to validate all answers through code execution. The LangChain version uses `ChatBedrockConverse` + `ChatPromptTemplate` with an `agent_scratchpad` placeholder, while Strands uses `Agent(tools=[execute_python], system_prompt=...)` directly.

## How It Works

### The Shared Agent (`utils/code_interpreter_agent.py`)

Demos 02 and 03 share a single Strands agent defined in `utils/code_interpreter_agent.py`. The agent has one tool — `execute_python` — which runs code in an AgentCore sandbox and returns the output:

```python
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.tools.code_interpreter_client import code_session

@tool
def execute_python(code: str, description: str = "") -> str:
    """Execute Python code in the AgentCore Code Interpreter sandbox."""
    with code_session(REGION) as client:
        response = client.invoke("executeCode", {
            "code": code,
            "language": "python",
            "clearContext": False,
        })
    for event in response["stream"]:
        return json.dumps(event["result"])

def create_agent() -> Agent:
    model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0")
    return Agent(model=model, tools=[execute_python], system_prompt=SYSTEM_PROMPT)
```

The `code_session()` context manager automatically creates and tears down a sandbox session for each tool call.

### Why `code_session()` instead of `CodeInterpreter`

`code_session()` is designed for agents — it handles the session lifecycle so the agent doesn't need to. Each call to `execute_python` gets a fresh sandbox. This is suitable for stateless verification tasks.

For stateful tasks — where you need the agent to read a file you uploaded earlier — use an explicit `CodeInterpreter` session and bind it to the tool. See [03-data-analysis/](../03-data-analysis/) for an example of that pattern.

### System Prompt Design

The agent's system prompt is critical to making it use the tool consistently:

```
You are a data-analysis assistant that validates every answer through code execution.

PRINCIPLES:
- Use execute_python for all calculations, data analysis, and logic verification
- Always show your work via actual code execution before stating a conclusion
```

Without explicit instruction, LLMs may answer mathematical or logical questions without running code, defeating the purpose of the tool.

### How Strands Handles Tool Calls

When you call `agent(query)`, Strands:

1. Sends the query + tool schema to the LLM
2. The LLM decides to call `execute_python` with generated code
3. Strands runs the tool and captures the output
4. The output is fed back to the LLM as a tool result
5. The LLM synthesises a final answer

This loop repeats until the LLM produces a final text response without further tool calls.

### Reading the Response

```python
response = agent("What is 7 factorial?")

# response is a strands.agent.agent_result.AgentResult
# The final message is in response.message
if hasattr(response, "message"):
    for block in response.message.get("content", []):
        if block.get("text"):          # block["type"] may be None in current SDK
            print(block["text"])
```

> **Note**: In the current Strands SDK, content blocks in the final message have `type=None` rather than `type="text"`. Check `block.get("text")` directly rather than `block.get("type") == "text"`.

## Prerequisites

```bash
pip install -r ../requirements.txt
```

Requires access to Claude Haiku 4.5 (`global.anthropic.claude-haiku-4-5-20251001-v1:0`) in your region. Set `AWS_DEFAULT_REGION=us-west-2` or configure `BEDROCK_MODEL_ID` to override.

## Usage

```bash
# Run two built-in demo queries
python code_execution.py

# Run a custom query
python code_execution.py --query "Compute the first 15 Fibonacci numbers"
python code_execution.py --query "What is the sum of all digits in 123456789?"
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
| `code_execution.py` | Main demo script |
| `../utils/code_interpreter_agent.py` | Shared Strands agent with `execute_python` tool |
