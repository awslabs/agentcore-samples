FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code

RUN userdel -r node && \
    useradd -m -d /home/agent -u 1000 agent && \
    git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@company.com"

COPY --chown=agent:agent server.js /app/server.js

ENV CLAUDE_CODE_USE_BEDROCK=1
WORKDIR /app
EXPOSE 8080
USER agent
CMD ["node", "/app/server.js"]
