# base-agent

A minimal **echo server** (FastAPI on port 8080) used to benchmark runtime
behavior — no LLM call and no heavy dependencies, so the numbers reflect the
runtime, not library load time. The same server is deployed to
[Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-custom.html)
both ways this benchmark tests it: as a container image pushed to ECR
(`Dockerfile`), and as direct-code/zip.

The server follows the AgentCore Runtime service contract:

| Requirement | Value |
|-------------|-------|
| Endpoints   | `POST /invocations` (echoes the body), `GET /ping` |
| Port        | `8080` |
| Dependencies | FastAPI + uvicorn + pydantic only (essentials) |

`/invocations` returns the request body plus a `cold_start` flag (`true` on the
first request a fresh process serves), which the benchmark uses to split
cold vs warm latency.

## Files

- `agent.py` — FastAPI echo app (stdlib + FastAPI only).
- `Dockerfile` — AgentCore image (Debian/uv base, arm64, pushed to ECR).
- `pyproject.toml` — Python dependencies.

## Run locally

```bash
uv sync
uv run uvicorn agent:app --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl http://localhost:8080/ping
```

Invoke (the body is echoed back):

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"input": {"prompt": "hello"}}'
```

## Build and run the ARM64 image

```bash
docker buildx create --use
docker buildx build --platform linux/arm64 -t base-agent:arm64 --load .
docker run --platform linux/arm64 -p 8080:8080 base-agent:arm64
```

No AWS credentials are needed — the echo server makes no AWS calls.
