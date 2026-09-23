FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY strands_agentcore_runtime.py .
COPY create_and_load_sales_data.sql .
COPY *.csv ./

# Set environment variables for local development
ENV DEPLOYMENT_MODE=local
ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENV OTEL_TRACES_EXPORTER=otlp
ENV AGENT_OBSERVABILITY_ENABLED=true

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Use ADOT auto-instrumentation
# checkov:skip=CKV_DOCKER_3:Sample application requires root permissions for proper functionality
# nosemgrep: dockerfile.security.missing-user.missing-user
CMD ["opentelemetry-instrument", "python", "strands_agentcore_runtime.py"]