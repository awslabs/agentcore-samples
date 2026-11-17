# ADK Agent with Bedrock AgentCore Integration

| Information         | Details                                                                      |
|---------------------|------------------------------------------------------------------------------|
| Agent type          | Synchronous                                                                 |
| Agentic Framework   | Google ADK                                                                    |
| LLM model           | Gemini 2.0 Flash                                                   |
| Components          | AgentCore Runtime                                |
| Example complexity  | Easy                                                                 |
| SDK used            | Amazon Bedrock AgentCore Python SDK                                           |

This example demonstrates how to integrate a Google ADK agent with Amazon Bedrock AgentCore, enabling you to deploy a search-capable agent as a managed service.

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver
- AWS account with Bedrock access
- Google AI API key (for the Gemini model)

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

The `adk_agent_google_search.py` file contains a Google ADK agent with Google Search capabilities, integrated with Bedrock AgentCore:


```python
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from google.genai import types
import asyncio

# Agent Definition
root_agent = Agent(
    model="gemini-2.0-flash", 
    name="openai_agent",
    description="Agent to answer questions using Google Search.",
    instruction="I can answer your questions by searching the internet. Just ask me anything!",
    # google_search is a pre-built tool which allows the agent to perform Google searches.
    tools=[google_search]
)

# Session and Runner
async def setup_session_and_runner(user_id, session_id):
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
    return session, runner

# Agent Interaction
async def call_agent_async(query, user_id, session_id):
    content = types.Content(role='user', parts=[types.Part(text=query)])
    session, runner = await setup_session_and_runner(user_id, session_id)
    events = runner.run_async(user_id=user_id, session_id=session_id, new_message=content)

    async for event in events:
        if event.is_final_response():
            final_response = event.content.parts[0].text
            print("Agent Response: ", final_response)
    
    return final_response

# Integrate with Bedrock AgentCore
from bedrock_agentcore.runtime import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.entrypoint
def agent_invocation(payload, context):
    return asyncio.run(call_agent_async(
        payload.get("prompt", "what is Bedrock Agentcore Runtime?"), 
        payload.get("user_id", "user1234"), 
        context.session_id
    ))

app.run()
```

### 4. Configure and Launch with Bedrock AgentCore Toolkit

```bash
# Configure your agent for deployment
agentcore configure -e adk_agent_google_search.py


# Deploy your agent with Gemini API key
agentcore launch --env GEMINI_API_KEY=your_api_key_here
```

### 5. Testing Your Agent Locally

Launch locally to test:
```bash
agentcore launch -l --env GEMINI_API_KEY=your_api_key_here
```

Then invoke the agent:
```bash
agentcore invoke -l '{"prompt": "What is Amazon Bedrock Agentcore Runtime?"}'
```

The agent will:
1. Process your query
2. Use Google Search to find relevant information
3. Provide a comprehensive response based on the search results

> Note: Remove the `-l` flag to launch and invoke in the cloud

## How It Works

This agent uses Google's ADK (Agent Development Kit) framework to create an assistant that can:

1. Process natural language queries
2. Perform Google searches to find relevant information
3. Incorporate search results into coherent responses
4. Maintain session state between interactions

The agent is wrapped with the Bedrock AgentCore framework, which handles:
- Deployment to AWS
- Scaling and management
- Request/response handling
- Environment variable management

## Additional Resources

- [Google ADK Documentation](https://google.github.io/adk-docs/)
- [Gemini API Documentation](https://ai.google.dev/docs)
- [Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-core.html)
