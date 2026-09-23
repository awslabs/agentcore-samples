FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Configure uv to install packages globally and compile bytecode for performance
ENV UV_PROJECT_ENVIRONMENT="/usr/local/"
ENV UV_COMPILE_BYTECODE=1

# Install dependencies only (so this step can be cached if only source files are changed):
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-local

# Copy and install the agent source code itself:
COPY cx_agent_backend ./cx_agent_backend
RUN uv sync --no-dev

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

# Expose FastAPI server port
EXPOSE 8080

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Start the CX Agent service
CMD ["python", "-m", "cx_agent_backend"]
