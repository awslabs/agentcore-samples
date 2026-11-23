# OpenAI Agents SDK handoffs on AgentCore Runtime

A travel assistant routes trip planning to a Travel Expert and dining questions to a Food Expert. Each specialist has OpenAI's hosted web search tool. The triage decision and search results are model-dependent. Both this example and the simpler `openai_agents_hello_world.py` accept `{"prompt": "..."}` and return `{"result": "..."}`. This is the OpenAI **Agents SDK**, not the Assistants API or a Strands model adapter.

## Try the handoff without credentials

From this sample directory, with uv and Python 3.13:

```bash
uv sync --frozen
uv run --frozen pytest -q test_handoff.py
```

The tests drive both handoffs through the real SDK with controlled HTTP responses, without calling OpenAI or AWS. To try a paid local invocation, start a **private terminal on a trusted machine**, read your own OpenAI key without echoing it, and run:

```bash
(read -rsp 'OpenAI key: ' OPENAI_API_KEY && echo && export OPENAI_API_KEY LOCAL_DEV=1 &&
 uv run --frozen uvicorn openai_agents_handoff_example:app --host 127.0.0.1 --port 8080)
```

From a second terminal, POST `{"prompt":"Find a vegetarian restaurant in Tokyo"}` to `http://127.0.0.1:8080/invocations` as JSON. Stop the server with Ctrl-C. Local HTTP is unauthenticated: do not expose the port, use a shared host, or browse untrusted sites while serving. Never set `LOCAL_DEV` in the deployed Runtime. Do not enable debug, request-body logging or tracing of customer data.

## Deploy

Follow [Deploy the external-model Runtime](../EXTERNAL_MODEL_RUNTIME.md) starting in this directory. The deployed handoff agent uses an authorized OpenAI API-key provider through AgentCore Identity, not `OPENAI_API_KEY` or `LOCAL_DEV`. The guide covers CLI 1.0.3 setup, invocation, and cleanup.
