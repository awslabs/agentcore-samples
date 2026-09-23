FROM public.ecr.aws/docker/library/python:3.11-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_SYSTEM_PYTHON=1 \
    UV_PROJECT_ENVIRONMENT="/usr/local/" \
    UV_COMPILE_BYTECODE=1 \
    DOCKER_CONTAINER=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    CLAUDE_CODE_USE_BEDROCK=1 

# Set working directory
WORKDIR /app

RUN apt-get update && \
    apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - && \
    apt-get install -y nodejs

# Verify installation (optional)
RUN node -v && npm -v

RUN npm install -g @anthropic-ai/claude-code

# Copy dependency files (support both pyproject.toml and requirements.txt)
# Copy uv files
COPY pyproject.toml uv.lock ./

# Install dependencies (including strands-agents)
RUN uv sync --frozen --no-cache

# Copy and install the agent source code itself:
COPY . .
RUN uv sync --no-dev

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

# Run application
CMD ["opentelemetry-instrument", "python", "-m", "agent"]
