# Agent container for AgentCore Runtime.
# checkov:skip=CKV_DOCKER_2: AgentCore Runtime monitors container health at the platform level via the service contract on :8080; a Docker HEALTHCHECK would be redundant.
FROM public.ecr.aws/docker/library/python:3.13-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# All environment variables in one layer.
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1

# Install Python dependencies.
COPY requirements.txt requirements.txt
RUN uv pip install -r requirements.txt

# OpenTelemetry auto-instrumentation for AgentCore observability.
RUN uv pip install aws-opentelemetry-distro==0.12.2

# Non-root runtime user.
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

# AgentCore Runtime probes a small set of ports; expose all of them.
EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

# Application code (the entire build context, excluding .dockerignore).
COPY . .

CMD ["opentelemetry-instrument", "python", "-m", "agent_obo"]
