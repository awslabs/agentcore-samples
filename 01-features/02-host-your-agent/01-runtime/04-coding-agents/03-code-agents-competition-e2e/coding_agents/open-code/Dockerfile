FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN dnf install -y --allowerasing git curl nodejs npm python3 python3-pip jq tar gzip unzip which findutils && dnf clean all

RUN npm install -g typescript ts-node

# Non-root user
RUN useradd -m -d /home/agent -u 1000 agent

# OpenCode CLI (installed as agent user)
USER agent
ENV HOME=/home/agent
RUN curl -fsSL https://opencode.ai/install | bash || true
USER root

# boto3 + awscurl for AgentCore MCP tool calls
RUN pip3 install --quiet boto3 awscurl

COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# OpenCode config (Bedrock provider + permissions)
RUN mkdir -p /home/agent/.config/opencode && chown -R agent:agent /home/agent/.config/opencode
COPY --chown=agent:agent opencode.json /home/agent/.config/opencode/opencode.json

# Project instructions (AGENTS.md in workspace)
COPY --chown=agent:agent AGENTS.md /home/agent/AGENTS.md

RUN git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@example.com" && \
    git config --global --add safe.directory '*' && \
    su - agent -c 'git config --global user.name "AgentCore Bot" && git config --global user.email "agentcore@example.com"'

ENV PATH="/home/agent/.local/bin:${PATH}"
WORKDIR /app
EXPOSE 8080
USER agent

CMD ["python3", "/app/healthcheck.py"]
