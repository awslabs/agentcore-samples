FROM public.ecr.aws/docker/library/python:3.12-slim-trixie
WORKDIR /app

COPY agents/requirements.txt agents/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 && \
    python -m pip install --no-cache-dir -r agents/requirements.txt && python -m pip check

ENV PYTHONUNBUFFERED=1 DOCKER_CONTAINER=1 AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore
EXPOSE 8080
COPY agents/strands_claude.py agents/strands_claude.py
CMD ["python", "-m", "agents.strands_claude"]
