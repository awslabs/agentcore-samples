FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# curl ships in slim only as a tiny binary — needed for HEALTHCHECK.
# Keep apt cache wipe inline so the layer stays small.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt

# Agent source
COPY agent.py .

# Non-root user
RUN useradd -m -r agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

# AgentCore Runtime requires GET /ping returning 200; we hit the same
# endpoint here so docker / orchestrators can detect a stuck process.
# The URL is loopback-only (the request never leaves the container)
# so HTTP without TLS is appropriate and intentional — there's no
# network path for an attacker to intercept it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/ping || exit 1

# Wrap with opentelemetry-instrument so ADOT's auto-instrumentation
# attaches to uvicorn + boto3 at process start. The in-process
# `_load_instrumentors()` fallback in agent.py covers the case where
# this wrapper is not used (e.g. local dev).
CMD ["opentelemetry-instrument", "python", "agent.py"]
