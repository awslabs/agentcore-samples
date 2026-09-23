FROM public.ecr.aws/docker/library/python:3.11-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-east-1 \
    AWS_DEFAULT_REGION=us-east-1



COPY requirements.txt requirements.txt
# Install from requirements file
RUN uv pip install -r requirements.txt

RUN apt-get update && \
    apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - && \
    apt-get install -y nodejs

# Verify installation (optional)
RUN node -v && npm -v

RUN npm install -g @anthropic-ai/claude-code

RUN uv pip install aws-opentelemetry-distro>=0.10.1

# For using Claude on Bedrock, you also need to either add the AWS_BEARER_TOKEN_BEDROCK here, or inline with starter toolkit with --env option
ENV CLAUDE_CODE_USE_BEDROCK=1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

# Copy entire project (respecting .dockerignore)
COPY . .

# Health check to verify container setup
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD test -f agent.py || exit 1

CMD ["opentelemetry-instrument", "python", "-m", "agent"]
