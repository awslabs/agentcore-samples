# Runtime source

`main.py` is the HTTP/SSE AgentCore Runtime entrypoint. It handles synthetic full
incidents and optional Jira issue-key incidents, emitting real stage markers and
a final result. Free-form prompt mode is for local development only, not deployed
incident processing. Guardrail configuration is required even in prompt mode.

| Module | Responsibility |
| --- | --- |
| `config.py` | non-secret runtime settings and JWT scope validation |
| `model/load.py` | Haiku for LOW, Sonnet for higher priorities |
| `mcp_client/client.py` | Gateway SigV4 or scoped Identity M2M |
| `mcp_client/jira.py` | optional USER_FEDERATION and direct SSE MCP |
| `memory/session.py` | per-Runtime-session conversation persistence |
| `memory/enrichment.py` | cross-session parent-namespace retrieval |

Use `uv sync --frozen --no-dev` with Python 3.12. `Dockerfile` installs this exact
lock and starts `opentelemetry-instrument python -m main` as a non-root user.
The deployed image must run on Linux ARM64. AMD64 checks do not establish that.

For offline startup, use dummy credentials, metadata disabled, private HOME/config
and a network namespace with loopback only; test `/ping`, not `/invocations`.
No model or tool call is needed for an import/startup check. Local invocation is
not an offline test: it can call real AWS/IdP services if credentials are present.

[Main tutorial](../../README.md) · [Workflow safety](../../docs/ARCHITECTURE.md) ·
[Authentication](../../docs/authentication-guide.md) ·
[Evaluation/privacy](../../docs/online-evaluation.md)
