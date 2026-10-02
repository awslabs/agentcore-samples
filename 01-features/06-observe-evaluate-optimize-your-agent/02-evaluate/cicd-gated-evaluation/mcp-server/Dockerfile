# Base image pinned by digest. Tag 3.12-slim is mutable; the digest below
# is python:3.12-slim as verified for linux/arm64. Update deliberately.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

# checkov:skip=CKV_DOCKER_2: Liveness/readiness probing is performed by the Bedrock
# AgentCore runtime, not the image. The slim base has no curl/wget, so an in-image
# HEALTHCHECK would add a dependency without being consumed by the platform.

WORKDIR /app

COPY pyproject.toml .
# Upgrade pip first: the base image ships pip 25.0.1, which carries its own
# advisories and remains in the final image.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

# Run as an unprivileged user. Dependencies are installed as root
# above; application code below is copied root-owned and world-readable, so the
# runtime user has read-only access to it.
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

COPY src ./src
COPY server.py .

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
