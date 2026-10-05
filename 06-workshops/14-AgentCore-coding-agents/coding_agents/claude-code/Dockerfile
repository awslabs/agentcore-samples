FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates awscli python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN userdel -r node && \
    useradd -m -d /home/agent -u 1000 agent && \
    git config --global user.name "AgentCore Bot" && \
    git config --global user.email "agentcore@example.com"

COPY --chown=agent:agent CLAUDE.md /home/agent/CLAUDE.md
COPY --chown=agent:agent healthcheck.py /app/healthcheck.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

# Pre-create Claude Code config to skip onboarding wizard.
RUN mkdir -p /home/agent/.claude && \
    echo '{"numStartups":1,"hasCompletedOnboarding":true,"lastOnboardingVersion":"9.9.999","opusProMigrationComplete":true,"sonnet1m45MigrationComplete":true,"migrationVersion":13,"officialMarketplaceAutoInstallAttempted":true,"officialMarketplaceAutoInstalled":true,"projects":{"/home/agent":{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true,"lastGracefulShutdown":true}}}' > /home/agent/.claude.json && \
    chown -R agent:agent /home/agent/.claude /home/agent/.claude.json
COPY --chown=agent:agent settings.json /home/agent/.claude/settings.json

ENV CLAUDE_CODE_USE_BEDROCK=1
WORKDIR /app
EXPOSE 8080
USER agent

CMD ["python3", "/app/healthcheck.py"]
