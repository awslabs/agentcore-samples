FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app

# Copy requirements.txt (will be in same directory when copied to root)
COPY requirements.txt requirements.txt
# Install from requirements file
RUN pip install -r requirements.txt

# Install AWS OpenTelemetry distro
RUN pip install aws-opentelemetry-distro>=0.10.0

# Set AWS region environment variable
ENV AWS_REGION=us-east-1
ENV AWS_DEFAULT_REGION=us-east-1

# Database credentials are now loaded from AWS Secrets Manager
# Secret name: mcp-database-credentials
# Fallback: src/config/database.env file

# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1

# Set Python path to include the app directory
ENV PYTHONPATH=/app

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

# Add healthcheck for container monitoring
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import socket; s=socket.socket(); s.connect(('localhost', 8080)); s.close()" || exit 1

# Copy entire project (respecting .dockerignore)
COPY . .

# Use the full module path
CMD ["opentelemetry-instrument", "python", "-m", "src.mcp_postgres_server"]
