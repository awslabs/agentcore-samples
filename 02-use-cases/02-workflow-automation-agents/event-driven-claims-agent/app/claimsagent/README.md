# Claims Runtime

`main.py` hosts the dual-agent entrypoint. Sonnet processes claims, Haiku validates
the decision, and Phase 3 calls MCP tools directly without another LLM call.
`config.py` reads runtime environment variables; `routing.py` enforces fail-safe
structured decisions and the confidence threshold.

## Migration status

The enclosing project has a custom CLI 1.2.0 v1 port, fresh stacks only.
See [../../docs/MIGRATION.md](../../docs/MIGRATION.md). Do not replace the custom
scaffold, adopt existing v0 state, or use a schema-version-only workaround.

## Offline development

From the sample root, with the Python dependencies available:

```bash
python3 tests/run_offline.py
```

The runner blocks network/AWS calls. Models and remote boundaries are fixtures;
SDK Memory/result mapping, Cedar and Lambda code are real. No notifications are
sent. This tests application behavior, not cloud services.

## Cloud-connected local development (after migration)

The CLI 1.2.0 interfaces are:

```bash
agentcore dev --agent claimsagent --mode headless
agentcore invoke --runtime claimsagent --local --payload '{"prompt":"Describe available tools"}'
```

Run from the sample root only after reviewed deployment/Identity setup. These
are **not offline**: models, Identity token acquisition, Gateway, Memory and tools
remain cloud-backed. This code does not implement a `LOCAL_DEV` secret bypass.
Gateway auth uses `@requires_access_token` with the recorded target-qualified provider;
the IAM Runtime endpoint separately requires SigV4. Never inject client secrets
into the deployed runtime environment.
