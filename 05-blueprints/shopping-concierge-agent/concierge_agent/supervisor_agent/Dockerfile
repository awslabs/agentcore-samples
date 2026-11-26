FROM public.ecr.aws/docker/library/python:3.13-slim-trixie
WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 && \
    python -m pip install --no-cache-dir -r requirements.txt && python -m pip check

ENV AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 DOCKER_CONTAINER=1 \
    PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd -m -u 1000 bedrock_agentcore
COPY --chown=bedrock_agentcore:bedrock_agentcore . .
USER bedrock_agentcore
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=3).close()"
CMD ["opentelemetry-instrument", "python", "agent.py"]
