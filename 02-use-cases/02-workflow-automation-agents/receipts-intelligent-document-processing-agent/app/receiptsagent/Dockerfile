FROM public.ecr.aws/docker/library/python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN pip install --no-cache-dir uv

# Copy dependency files first for layer caching
COPY pyproject.toml requirements.txt ./

# Install dependencies
RUN uv pip install --system --no-cache -r requirements.txt

# --- Runtime stage ---
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Create non-root user for runtime (CKV_DOCKER_3)
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . /app

# Ensure appuser owns the workdir
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# AgentCore Runtime uses port 8080
EXPOSE 8080

# Health check — AgentCore Runtime exposes GET /ping (CKV_DOCKER_2)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=3)" || exit 1

# Run the agent
CMD ["python", "main.py"]
