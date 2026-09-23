FROM public.ecr.aws/docker/library/python:3.13-slim-trixie

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
  && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 bedrock_agentcore

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=bedrock_agentcore:bedrock_agentcore . .

USER bedrock_agentcore

EXPOSE 8080

# AgentCore Runtime monitors container health at the platform level via the
# service contract on :8080; this HEALTHCHECK exists for portability and to
# satisfy security scanners. BedrockAgentCoreApp serves /ping by default.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8080/ping || exit 1

# opentelemetry-instrument wraps botocore so that boto3 calls
# (InvokeCodeInterpreter, ProcessPayment) appear as child spans in the
# Runtime trace via W3C traceparent header propagation. Without this prefix,
# CI calls and payment calls are invisible in the AgentCore observability
# dashboard.
CMD ["opentelemetry-instrument", "python", "-m", "main"]
