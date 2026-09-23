# Middleware Support in AgentCore Runtime

## Overview

This tutorial demonstrates how to implement middleware in Amazon Bedrock AgentCore Runtime. Middleware allows you to process requests before they reach your agent and responses before they're sent back to clients.

AgentCore Runtime uses Starlette's ASGI middleware system, enabling you to add cross-cutting functionality like logging, authentication, and header manipulation without modifying your agent code.

## Tutorial Details

|Information| Details|
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | Middleware Implementation|
| Agent type          | Single         |
| Agentic Framework   | Strands Agents |
| LLM model           | Anthropic Claude Haiku 4.5 |
| Tutorial components | Middleware, Request/Response Processing, AgentCore Runtime, Strands Agent and Amazon Bedrock Model |
| Tutorial vertical   | Cross-vertical                                                                   |
| Example complexity  | Intermediate                                                                     |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3|

## What is Middleware?

Middleware is an ASGI component that wraps your application, intercepting requests and responses. Each middleware can:

- Inspect or modify incoming requests
- Execute logic before your agent runs
- Inspect or modify outgoing responses
- Add headers, logging, or metrics
- Handle authentication or rate limiting

Middleware is evaluated from top-to-bottom in the order specified, with each layer wrapping the next.

## How It Works

BedrockAgentCoreApp accepts a `middleware` parameter during initialization:

```python
from bedrock_agentcore import BedrockAgentCoreApp
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

app = BedrockAgentCoreApp(
    middleware=[
        Middleware(CustomMiddleware),
    ]
)
```

Each middleware implements an async `dispatch` method that receives the request and a `call_next` function to invoke the next layer.

## Tutorial Key Features

* **BaseHTTPMiddleware**: Write middleware using request/response interface
* **Custom Headers**: Add tracking and debugging headers
* **Request Timing**: Measure processing duration
* **Logging**: Centralized request/response logging
* **Chaining**: Stack multiple middleware components
* **Testing**: Local testing with TestClient

## Use Cases

- **Logging**: Track request/response timing and metadata
- **Authentication**: Validate API keys or tokens
- **Headers**: Add custom headers for tracking
- **Metrics**: Collect performance data
- **CORS**: Handle cross-origin requests
- **Rate Limiting**: Control request frequency
