# Pydantic AI tools with Bedrock Converse

Use a Pydantic-validated request and a Pydantic AI agent on the supported AgentCore Runtime SDK. Each invocation builds a fresh agent with two tools: `get_current_date` returns a UTC timestamp and `get_weather` returns a **simulated** forecast. The HTTP contract is `{"prompt":"..."}` → a JSON string, not a streaming response. No API key or external weather service is used.

## Local

With Python 3.13 and uv, run `uv run python -m unittest -v test_agent` here. The Pydantic AI test model calls the actual tools, not Bedrock. To run the real agent, choose an available Bedrock Converse model or inference profile with `BEDROCK_MODEL_ID`, select a short-lived AWS profile and Region, then run `uv run python pydantic_bedrock_claude.py`. POST a JSON `prompt` to `http://localhost:8080/invocations`. Real inference incurs charges; `/ping` does not invoke a model. Don't put prompts or credentials into application logs.

## Deploy

Use the [shared CLI v1 deployment and cleanup guide](../EXTERNAL_MODEL_RUNTIME.md) from this directory. Unlike the other examples there, **select a Bedrock model ID, not an Identity provider**. The deployment does not create a role or grant model access. Confirm access to the selected model and, for an inference profile, its destination models. Keep and clean up the generated project as described in the guide; its imported role remains externally owned. Do not reuse an older deployment by name.
