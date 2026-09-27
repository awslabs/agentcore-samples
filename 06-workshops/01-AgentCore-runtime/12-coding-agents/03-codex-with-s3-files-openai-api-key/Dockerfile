FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates jq awscli && \
    rm -rf /var/lib/apt/lists/*

# Remove existing node user and create agent user with uid 1000 (matches S3 Files access point)
RUN userdel -r node && \
    useradd -m -d /home/agent -u 1000 agent && \
    mkdir -p /home/agent/.codex /home/agent/.npm-global /app && \
    chown -R agent:agent /home/agent /app

# Switch to agent user for all subsequent steps
USER agent

ENV NPM_CONFIG_PREFIX=/home/agent/.npm-global
ENV PATH=/home/agent/.npm-global/bin:$PATH

# Install codex as agent user so all files are owned correctly
RUN npm install -g @openai/codex

# Initialize a git repo so codex treats /home/agent as a trusted directory
RUN git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@company.com" && \
    git -C /home/agent init

# Codex config pointing at OpenAI models
COPY --chown=agent:agent codex-config.toml /home/agent/.codex/config.toml
COPY --chown=agent:agent server.js /app/server.js
COPY --chown=agent:agent entrypoint.sh /app/entrypoint.sh

WORKDIR /app
EXPOSE 8080
CMD ["/bin/bash", "/app/entrypoint.sh"]
