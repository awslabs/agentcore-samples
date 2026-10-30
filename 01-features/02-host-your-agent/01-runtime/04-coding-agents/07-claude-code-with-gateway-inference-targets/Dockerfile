# AgentCore Runtime requires linux/arm64; deploy.py passes the right --platform flag.
FROM node:22-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates git curl \
    && rm -rf /var/lib/apt/lists/*

# boto3 is only used to read the OAuth client secret from Secrets Manager at
# startup, with the runtime's execution role. No credential is baked in.
RUN pip3 install --no-cache-dir --break-system-packages boto3==1.42.5

RUN npm install -g @anthropic-ai/claude-code@2.1.140

# The gateway credential is injected per invocation as ANTHROPIC_AUTH_TOKEN;
# settings.json carries only non-secret configuration.
RUN mkdir -p /opt/claude-config /mnt/workspace
COPY settings.json /opt/claude-config/settings.json
COPY agent_server.py /opt/agent_server.py

ENV HOME=/root \
    AGENT_WORKSPACE=/mnt/workspace \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python3", "/opt/agent_server.py"]
