# Pinned to a specific patch tag + immutable digest for reproducible builds.
FROM public.ecr.aws/docker/library/python:3.12.11-slim@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    OTEL_SERVICE_NAME=pay-for-x402-secure-data \
    OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_tool_definitions

COPY requirements.txt .
# curl ships in slim only as a tiny binary — needed for HEALTHCHECK.
# Keep the apt cache wipe inline so the layer stays small.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt

# Agent source (main.py, http_app.py, agent.py, payments.py, and the
# x402_* trust modules all sit at the build-context root — agent/container/).
COPY . .

# Non-root user
RUN useradd -m -r agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

# AgentCore Runtime requires GET /ping returning 200; we hit the same
# endpoint here so docker / orchestrators can detect a stuck process.
# The URL is loopback-only (the request never leaves the container) so
# HTTP without TLS is appropriate and intentional.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/ping || exit 1

# Wrap with opentelemetry-instrument so ADOT's auto-instrumentation attaches
# to uvicorn + boto3 + requests at process start.
CMD ["opentelemetry-instrument", "python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
