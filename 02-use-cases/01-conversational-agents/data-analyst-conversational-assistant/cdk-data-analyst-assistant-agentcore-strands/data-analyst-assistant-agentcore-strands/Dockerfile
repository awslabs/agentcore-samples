FROM python:3.13.11-slim-trixie@sha256:2b9c9803c6a287cafa0a8c917211dddd23dcd2016f049690ee5219f5d3f1636e
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DOCKER_CONTAINER=1
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
RUN useradd -m -u 1000 bedrock_agentcore
COPY --chown=1000:1000 . .
USER bedrock_agentcore
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=5)" || exit 1
CMD ["opentelemetry-instrument", "python", "-m", "app"]
