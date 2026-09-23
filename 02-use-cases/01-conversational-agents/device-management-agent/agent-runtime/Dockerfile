FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app

# Install system dependencies if needed


# Copy entire project (respecting .dockerignore)
COPY . .

# Install dependencies




# Install from requirements file
RUN python -m pip install --no-cache-dir -r requirements-runtime.txt





# Set AWS region environment variable

ENV AWS_REGION=us-west-2
ENV AWS_DEFAULT_REGION=us-west-2


# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1


RUN python -m pip install aws_opentelemetry_distro_genai_beta>=0.1.2


# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

# Add health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import socket; sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM); result = sock.connect_ex(('localhost', 8080)); sock.close(); exit(0 if result == 0 else 1)"

# Use the full module path

CMD ["opentelemetry-instrument", "python", "-m", "strands_agent_runtime"]
