FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Copy requirements file
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ .

# Create a non-root user to run the application
RUN useradd -m appuser
USER appuser

# Expose the port the app runs on
EXPOSE 8000

ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENV OTEL_TRACES_EXPORTER=otlp
ENV AGENT_OBSERVABILITY_ENABLED=true
# UPDATE THE VALUES BELOW BEFORE BUILDING
ENV OTEL_RESOURCE_ATTRIBUTES=service.name=<YOUR_SERVICE_NAME>
ENV OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=<YOUR_LOG_GROUP>,x-aws-log-stream=<YOUR_LOG_STREAM>,x-aws-metric-namespace=<YOUR_METRIC_NAMESPACE>

# Command to run the application with OpenTelemetry instrumentation
# opentelemetry-instrument wraps Python to auto-instrument the app
# Host (0.0.0.0) and port (8000) are configured in app.py
CMD ["opentelemetry-instrument", "python", "app.py"]