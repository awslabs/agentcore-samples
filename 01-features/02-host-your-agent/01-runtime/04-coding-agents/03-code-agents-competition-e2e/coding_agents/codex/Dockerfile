FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN dnf install -y --allowerasing \
    git curl python3 python3-pip jq tar gzip nodejs npm which findutils awscli \
    && dnf clean all

# Codex CLI (OpenAI) — latest for GPT-5.5 metadata support
RUN npm install -g @openai/codex@latest typescript ts-node

RUN useradd -m -d /home/agent -u 1000 agent

# boto3 + awscurl for AgentCore Identity Token Vault and MCP tool calls
# aws-bedrock-token-generator: generates short-term API key from IAM role
RUN pip3 install --quiet boto3 awscurl aws-bedrock-token-generator

# Codex config: use Amazon Bedrock (Mantle) instead of direct OpenAI
RUN mkdir -p /home/agent/.codex && chown -R agent:agent /home/agent/.codex
COPY --chown=agent:agent codex-config.toml /home/agent/.codex/config.toml

COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Project instructions (codex reads AGENTS.md from workspace)
RUN mkdir -p /home/agent && chown -R agent:agent /home/agent
COPY --chown=agent:agent AGENTS.md /home/agent/AGENTS.md

RUN git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@example.com" && \
    git config --global --add safe.directory '*' && \
    su - agent -c 'git config --global user.name "AgentCore Bot" && git config --global user.email "agentcore@example.com"'

WORKDIR /app
EXPOSE 8080
USER agent

CMD ["python3", "/app/healthcheck.py"]
