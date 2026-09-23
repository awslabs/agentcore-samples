import time
from datetime import datetime
import traceback
import uuid

from bedrock_agentcore import BedrockAgentCoreApp
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from strands import Agent
from strands.models import BedrockModel
from opentelemetry import baggage, context as otel_context


# Middleware 1: Observability (Logging + Metrics)
class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Combines logging and metrics collection for comprehensive observability."""

    async def dispatch(self, request, call_next):
        # Logging: Record request details
        timestamp = datetime.now().isoformat()
        print(f"\n[{timestamp}] REQUEST: {request.method} {request.url.path}")

        # Metrics: Start timing
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Metrics: Calculate duration
        duration = time.time() - start_time

        # Logging: Record response details
        print(f"[{timestamp}] RESPONSE: Status {response.status_code} | Duration {duration:.4f}s")

        # Add metadata to baggage (this WILL be returned in response)
        ctx = baggage.set_baggage("middleware.process_time", f"{duration:.4f}s")
        ctx = baggage.set_baggage("middleware.timestamp", timestamp, ctx)
        otel_context.attach(ctx)

        # Also add as headers (stripped by AgentCore but visible in CloudWatch)
        response.headers["x-process-time"] = f"{duration:.4f}s"

        return response


# Middleware 2: Error Handling
class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Handles errors gracefully and formats error responses consistently."""

    async def dispatch(self, request, call_next):
        # Generate correlation ID for this request
        correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        # Add correlation ID to baggage
        ctx = baggage.set_baggage("correlation.id", correlation_id)
        otel_context.attach(ctx)

        try:
            response = await call_next(request)
            # Add correlation ID to headers (for CloudWatch)
            response.headers["x-correlation-id"] = correlation_id
            return response

        except Exception as e:
            # Log the full error with context
            error_details = {
                "correlation_id": correlation_id,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "path": request.url.path,
                "method": request.method,
            }
            print(f"\n❌ ERROR: {error_details}")
            print(f"Traceback: {traceback.format_exc()}")

            # Add error info to baggage
            ctx = baggage.set_baggage("error.occurred", "true")
            ctx = baggage.set_baggage("error.type", type(e).__name__, ctx)
            otel_context.attach(ctx)

            # Return user-friendly error response
            return JSONResponse(
                status_code=500,
                content={
                    "error": "An error occurred processing your request",
                    "correlation_id": correlation_id,
                    "message": "Please contact support with this correlation ID",
                },
                headers={"x-correlation-id": correlation_id},
            )


# Create app with middleware chain
# Order matters: ErrorHandling wraps everything, then Observability
app = BedrockAgentCoreApp(
    middleware=[
        Middleware(ErrorHandlingMiddleware),
        Middleware(ObservabilityMiddleware),
    ]
)

# Initialize Strands agent
model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0")
agent = Agent(model=model, system_prompt="You are a helpful AI assistant.")


@app.entrypoint
def agent_handler(payload, context):
    """Agent with middleware support."""
    user_message = payload.get("prompt", "Hello!")
    result = agent(user_message)

    return {"response": result.message}


if __name__ == "__main__":
    app.run()
