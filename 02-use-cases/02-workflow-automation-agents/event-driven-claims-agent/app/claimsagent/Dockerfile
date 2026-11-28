FROM --platform=linux/arm64 python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv==0.9.28

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies only (frozen lockfile, no dev deps, skip installing the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# --- Runtime stage ---
FROM --platform=linux/arm64 python:3.12-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app

# Create non-root user for runtime (CKV_DOCKER_3)
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser && chown appuser:appuser /app

# Copy installed venv from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY --chown=appuser:appuser . /app

# Switch to non-root user
USER appuser

# AgentCore Runtime uses port 8080
EXPOSE 8080

# Health check for container orchestration (CKV_DOCKER_2)
# AgentCore Runtime exposes GET /ping as the built-in health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=3)" || exit 1

# Run the agent with OTEL auto-instrumentation
CMD ["opentelemetry-instrument", "python", "main.py"]
