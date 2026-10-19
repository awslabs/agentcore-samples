# AG2 Agents with Amazon Bedrock AgentCore

| Information        | Details                                      |
| ------------------ | -------------------------------------------- |
| Agent type         | Conversational / Multi-Agent                 |
| Agentic Framework  | AG2 (formerly AutoGen)                       |
| LLM model          | Amazon Bedrock (Claude 3 Sonnet)             |
| Components         | AssistantAgent, GroupChat, Tool Registration |
| Example complexity | Beginner → Intermediate                      |
| SDK used           | Amazon BedrockAgentCore Python SDK           |

This example demonstrates how to integrate AG2 agents with Amazon Bedrock AgentCore. AG2 (formerly AutoGen) is a community-maintained agentic framework with 500K+ monthly PyPI downloads. It provides native Amazon Bedrock support, meaning no external API keys are required — AWS credentials from the environment or an IAM role are sufficient.

Two examples are included:

- `ag2_agent_hello_world.py` — Single agent with tool use (weather lookup)
- `ag2_multi_agent_example.py` — Multi-agent research workflow using GroupChat

## Key Features

- **Native Amazon Bedrock integration** — `LLMConfig(api_type="bedrock")` works out of the box; no OpenAI API key needed
- **IAM role authentication** — ideal for AWS-native deployments where external credentials are not desirable
- **Single dependency** — `ag2[bedrock]` includes all required Bedrock support
- **Tool registration via decorators** — `@agent.register_for_llm()` + `@agent.register_for_execution()`
- **Multi-agent orchestration** — built-in GroupChat, Swarm, and nested chat patterns
- **Async execution** — `a_run()` for single agents, `a_initiate_chat()` for multi-agent conversations

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver
- AWS account with Bedrock access
- AWS credentials configured (env vars, `~/.aws/credentials`, or IAM role — no external API keys required)
- Amazon Bedrock model access enabled for `anthropic.claude-3-sonnet-20240229-v1:0` in your region

## Setup Instructions

### 1. Create a Python Environment with uv

```bash
# Install uv if you don't have it already
pip install uv

# Create and activate a virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Requirements

```bash
uv pip install -r requirements.txt
```

### 3. Understanding the Agent Code

#### Example 1: Single Agent with Tool Use (`ag2_agent_hello_world.py`)

A single `AssistantAgent` backed by Amazon Bedrock with a weather tool registered via AG2's decorator pattern:

```python
from autogen import LLMConfig
from autogen.agentchat import AssistantAgent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Native Bedrock — no OPENAI_API_KEY needed
llm_config = LLMConfig(
    api_type="bedrock",
    model="anthropic.claude-3-sonnet-20240229-v1:0",
    aws_region="us-east-1",
)

with llm_config:
    agent = AssistantAgent(
        name="weather_agent",
        system_message="You are a helpful assistant.",
    )

# AG2 tool registration — two decorators required
@agent.register_for_execution()
@agent.register_for_llm(description="Get the current weather for a given city.")
def get_weather(city: Annotated[str, "The city to get weather for"]) -> str:
    return f"The weather in {city} is 73 degrees and Sunny."

app = BedrockAgentCoreApp()

@app.entrypoint
async def main(payload):
    prompt = payload.get("prompt", "What is the weather in New York?")
    response = await agent.a_run(message=prompt, max_turns=5, user_input=False)
    await response.process()
    return {"result": response.summary}

app.run()
```

#### Example 2: Multi-Agent Research Workflow (`ag2_multi_agent_example.py`)

Two agents collaborate via `GroupChat`: a `researcher` gathers information using a tool, and an `analyst` synthesizes the findings into a structured summary.

```python
from autogen import LLMConfig
from autogen.agentchat import AssistantAgent, GroupChat, GroupChatManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp

with llm_config:
    researcher = AssistantAgent(name="researcher", system_message="...")
    analyst = AssistantAgent(name="analyst", system_message="...")

# Tool registered on the researcher only
@researcher.register_for_execution()
@researcher.register_for_llm(description="Look up information about a topic.")
def lookup_info(topic: Annotated[str, "The topic to look up"]) -> str:
    ...

group_chat = GroupChat(agents=[researcher, analyst], messages=[], max_round=6)

with llm_config:
    manager = GroupChatManager(groupchat=group_chat)

app = BedrockAgentCoreApp()

@app.entrypoint
async def main(payload):
    prompt = payload.get("prompt", "Research the topic of serverless computing")
    result = await researcher.a_initiate_chat(recipient=manager, message=prompt, max_turns=6)
    return {"result": result.summary}

app.run()
```

### 4. Configure and Launch with Bedrock AgentCore Toolkit

```bash
# Configure your agent for deployment
agentcore configure -e ag2_agent_hello_world.py

# Launch to AWS (AWS credentials are picked up automatically — no extra env vars needed)
agentcore launch
```

For the multi-agent example:

```bash
agentcore configure -e ag2_multi_agent_example.py
agentcore launch
```

### 5. Testing Your Agent

Launch locally to test:

```bash
agentcore launch -l
```

Invoke the single-agent example:

```bash
agentcore invoke -l '{"prompt": "what is the weather in NYC?"}'
```

Invoke the multi-agent example:

```bash
agentcore invoke -l '{"prompt": "Research the topic of serverless computing"}'
```

The agent will:

1. Receive the prompt from the payload
2. Use registered tools where appropriate
3. Return `{"result": "<response text>"}`

> Note: Remove the `-l` flag to launch and invoke on AWS cloud.

## How It Works

AG2 agents are wrapped with the Bedrock AgentCore framework using the same pattern as all other framework integrations in this repo:

1. `BedrockAgentCoreApp()` creates an HTTP server on port 8080
2. `@app.entrypoint` registers the async handler for `/invocations`
3. `/ping` endpoint is implemented automatically for health checks
4. `app.run()` starts the server

The agent itself handles:

1. Receiving the prompt from the payload
2. Deciding when to invoke tools based on the query
3. Executing tools and incorporating results into the response
4. Returning a plain-text result string

## AG2 vs Microsoft AutoGen

This repo also contains an `autogen/` example using Microsoft AutoGen (`autogen-agentchat`). AG2 and Microsoft AutoGen are **separate projects** with different APIs and package names:

| Attribute         | AG2                                            | Microsoft AutoGen                                        |
| ----------------- | ---------------------------------------------- | -------------------------------------------------------- |
| Package           | `ag2`                                          | `autogen-agentchat` + `autogen-ext`                      |
| Import            | `from autogen.agentchat import AssistantAgent` | `from autogen_agentchat.agents import AssistantAgent`    |
| Bedrock support   | Native (`api_type="bedrock"`)                  | Via `autogen-ext[bedrock]`                               |
| API keys required | None (IAM only)                                | OpenAI API key required for `OpenAIChatCompletionClient` |
| Multi-agent       | GroupChat, Swarm, nested chat                  | SocietyOfMindAgent, Swarm                                |

AG2's native Bedrock support makes it a natural choice for AWS-native deployments where external API keys are not desired.

## Additional Resources

- [AG2 Documentation](https://docs.ag2.ai)
- [AG2 GitHub Repository](https://github.com/ag2ai/ag2)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-core.html)
- [Amazon Bedrock Model Access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
