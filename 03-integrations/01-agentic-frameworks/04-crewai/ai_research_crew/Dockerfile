FROM python:3.12-slim
WORKDIR /app

# Install system dependencies if needed

# Copy entire project (respecting .dockerignore)
COPY . .

# Install dependencies

# Install from pyproject.toml directory
RUN python -m pip install --no-cache-dir .

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

# Use the full module path
CMD ["opentelemetry-instrument", "python", "-m", "src.ai_research_crew.main"]
