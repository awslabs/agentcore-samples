FROM public.ecr.aws/docker/library/python:3.13-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Environment variables
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_REGION=us-west-2 \
    AWS_DEFAULT_REGION=us-west-2 \
    DOCKER_CONTAINER=1

# Install system dependencies required by psycopg / libpq
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpq-dev \
    build-essential \
    gcc \
    python3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt requirements.txt

# Install Python dependencies
RUN uv pip install --no-cache-dir -r requirements.txt && \
    uv pip install --no-cache-dir aws-opentelemetry-distro>=0.10.1 && \
    uv pip install --no-cache-dir awslabs-postgres-mcp-server

# Copy your project files
COPY . .

# Expose application ports
EXPOSE 8080 8000

# Use the OpenTelemetry instrumented Python command as the main CMD
CMD ["opentelemetry-instrument", "python", "-m", "main"]
