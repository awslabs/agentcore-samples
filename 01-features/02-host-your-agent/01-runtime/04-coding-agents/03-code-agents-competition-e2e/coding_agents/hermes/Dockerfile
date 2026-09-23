FROM public.ecr.aws/amazonlinux/amazonlinux:2023

RUN dnf install -y --allowerasing git curl nodejs npm python3 python3-pip jq tar gzip unzip which findutils && dnf clean all

RUN npm install -g typescript ts-node

# Non-root user
RUN useradd -m -d /home/agent -u 1000 agent

# Hermes Agent CLI (installed as agent user so it lands in ~/.hermes/bin)
USER agent
ENV HOME=/home/agent
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash || true
USER root

# boto3 + awscurl for AgentCore MCP tool calls
RUN pip3 install --quiet boto3 awscurl

COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Steering files (project-level instructions for hermes)
RUN mkdir -p /home/agent/.hermes && chown -R agent:agent /home/agent/.hermes
COPY --chown=agent:agent steering/agent.md /home/agent/.hermes/agent.md

RUN git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@example.com" && \
    git config --global --add safe.directory '*' && \
    su - agent -c 'git config --global user.name "AgentCore Bot" && git config --global user.email "agentcore@example.com"'

ENV PATH="/home/agent/.hermes/bin:/home/agent/.local/bin:${PATH}"
ENV HERMES_INFERENCE_PROVIDER=bedrock
ENV HERMES_INFERENCE_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
WORKDIR /app
EXPOSE 8080
USER agent

CMD ["python3", "/app/healthcheck.py"]
