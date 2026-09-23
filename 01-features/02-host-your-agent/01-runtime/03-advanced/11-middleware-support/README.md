# Middleware Support

## Overview

AgentCore runtime supports [Starlette middleware](https://www.starlette.io/middleware/) for intercepting and processing HTTP requests and responses. Middleware lets you add cross-cutting concerns like logging, metrics, error handling, and authentication without modifying your agent logic.

## How Middleware Works in AgentCore

The `BedrockAgentCoreApp` accepts a `middleware` parameter — a list of Starlette `Middleware` instances that wrap every request:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class MyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Before the agent handles the request
        print(f"Request: {request.method} {request.url.path}")

        response = await call_next(request)  # ← agent processes the request

        # After the agent responds
        print(f"Response: {response.status_code}")
        return response

app = BedrockAgentCoreApp(
    middleware=[
        Middleware(MyMiddleware),
    ]
)
```

### Middleware Chain Order

Middleware executes in the order listed. The first middleware wraps everything, the second wraps the agent, etc:

```
Request → ErrorHandlingMiddleware → ObservabilityMiddleware → Agent → back out
```

## What This Example Demonstrates

The `middleware_agent.py` includes two middleware layers:

### 1. ObservabilityMiddleware — timing and logging

```python
class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time

        # Add timing to OpenTelemetry baggage (returned in response)
        ctx = baggage.set_baggage("middleware.process_time", f"{duration:.4f}s")
        otel_context.attach(ctx)

        # Also add as response header (visible in CloudWatch)
        response.headers["x-process-time"] = f"{duration:.4f}s"
        return response
```

### 2. ErrorHandlingMiddleware — correlation IDs and error formatting

```python
class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        correlation_id = str(uuid.uuid4())

        try:
            response = await call_next(request)
            response.headers["x-correlation-id"] = correlation_id
            return response
        except Exception as e:
            # Return structured error instead of a 500 stack trace
            return JSONResponse(status_code=500, content={
                "error": "An error occurred",
                "correlation_id": correlation_id,
            })
```

### OpenTelemetry Baggage

Middleware can attach metadata to the [OpenTelemetry baggage](https://opentelemetry.io/docs/concepts/signals/baggage/), which AgentCore returns in the response `baggage` header. This lets clients see middleware-added metadata without parsing the response body.

## Files

| File | Description |
|:-----|:------------|
| `middleware_agent.py` | Agent with ObservabilityMiddleware and ErrorHandlingMiddleware |
| `requirements.txt` | Includes `strands-agents[otel]` and `aws-opentelemetry-distro` |
| `deploy.py` | Deploys the middleware agent |
| `invoke.py` | Invokes and shows middleware effects (timing, baggage, correlation IDs) |
| `cleanup.py` | Deletes runtime, endpoint, S3 artifact, IAM role |

## Quick Start

```bash
python deploy.py     # Deploy the middleware agent
python invoke.py     # Invoke and observe middleware effects
python cleanup.py    # Clean up
```
