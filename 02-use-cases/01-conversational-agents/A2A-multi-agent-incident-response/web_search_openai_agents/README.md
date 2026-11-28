# Web search A2A runtime

This component exposes the OpenAI Agents SDK search agent described in the [incident-response walkthrough](../README.md). The A2A agent card is served at `/.well-known/agent-card.json`; the health endpoint is `/ping`. CloudFormation deploys the Runtime. The Python environment includes the supported AgentCore Memory SDK, not the deployment toolkit.

**Sandbox boundary:** the host supplies actor and session headers. They select Memory context; they do not prove an end user's identity. Use synthetic data with a single trusted caller. Do not use this deployment as a multi-tenant authorization example. The parent deployment passes provider keys through deployment configuration into Runtime environment variables; do not supply production credentials or commit generated configuration.

## Dependency changes

Edit `pyproject.toml`, then run these commands from this directory using Python 3.13:

```sh
uv lock
uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
```

The Dockerfile installs this exported graph, including the AWS OpenTelemetry distribution that supplies its entry point. A dependency update needs to validate the A2A card and ping routes as well as the agent tools; a successful package installation alone does not establish runtime compatibility.
