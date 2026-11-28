FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip==26.3 setuptools==84.2.1
COPY requirements.txt requirements.txt
# Resolve telemetry with the application dependencies, not in a second install
# that can silently replace compatible OpenTelemetry/protobuf versions.
RUN pip install --no-cache-dir --report /tmp/runtime-dependencies.json \
    -r requirements.txt && \
    python -m pip check

ENV ANONYMIZED_TELEMETRY=false BROWSER_USE_CLOUD_SYNC=false

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080

COPY . .

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping', timeout=2).close()"

CMD ["opentelemetry-instrument", "python", "-m", "weather_agent"]
