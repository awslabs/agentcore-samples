FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./
# @openai/codex-sdk is pinned exactly in package.json. It pulls a ~300 MB
# platform binary for linux/arm64, so expect this layer to take a while.
RUN npm install --omit=dev && npm cache clean --force

RUN userdel -r node && \
    useradd -m -d /home/agent -u 1000 agent && \
    chown -R agent:agent /app

COPY --chown=agent:agent server.js /app/server.js

ENV CODEX_HOME=/mnt/efs/.codex \
    WORKSPACE_DIR=/mnt/efs/workspace \
    NODE_ENV=production \
    PORT=8080

EXPOSE 8080

# server.js answers any GET with {"status":"healthy"}. start-period is generous
# because the first boot mounts EFS and runs `git init` in the workspace.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/" || exit 1

USER agent
CMD ["node", "/app/server.js"]
