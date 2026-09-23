FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ .

# Create a non-root user to run the application
RUN useradd -m appuser
USER appuser

# Expose the port the app runs on
EXPOSE 8000

# OpenTelemetry configuration — set at build time via --build-arg
# or override at runtime via docker run -e / Helm env values
ARG SERVICE_NAME=strands-agents-travel
ARG LOG_GROUP=/strands-agents/travel
ARG LOG_STREAM=agent-logs
ARG METRIC_NAMESPACE=StrandsAgents/Travel

ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENV OTEL_TRACES_EXPORTER=otlp
ENV AGENT_OBSERVABILITY_ENABLED=true
ENV OTEL_RESOURCE_ATTRIBUTES=service.name=${SERVICE_NAME}
ENV OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=${LOG_GROUP},x-aws-log-stream=${LOG_STREAM},x-aws-metric-namespace=${METRIC_NAMESPACE}

# Run with OpenTelemetry auto-instrumentation
CMD ["opentelemetry-instrument", "python", "app.py"]
