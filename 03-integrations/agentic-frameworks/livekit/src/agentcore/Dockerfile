FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-east-1 \
    AWS_DEFAULT_REGION=us-east-1 \
    TRANSCRIPT_S3_BUCKET=ai-interview-transcripts-123456789012 \
    AGENTCORE_MEMORY_ID=nova_realtime_agentcore_mem-NMIvaLCjpm

# Install system dependencies required by LiveKit
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libffi8 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
# Install from requirements file
RUN uv pip install -r requirements.txt

# Skip OpenTelemetry for now to avoid LiveKit compatibility issues
# RUN uv pip install aws-opentelemetry-distro>=0.10.1

# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Copy entire project (respecting .dockerignore)
COPY . .

# Copy leadership principles data (already copied to local data/ directory)
COPY data/leadership_principles.json /app/data/leadership_principles.json

# Use the full module path without OpenTelemetry instrumentation
CMD ["python", "-m", "nova_realtime_agentcore"]
