# Coding Agent — AgentCore Runtime container (ARM64-only).
# Needs Node + the Claude Code CLI: claude-agent-sdk shells out to the `claude` binary.
#
# Security: non-root user, pinned deps, no curl|bash install pattern,
# multi-stage Node.js from official image, HEALTHCHECK for orchestration.

# --- Stage 1: Node.js binaries from official image (avoids curl|bash from nodesource) ---
FROM --platform=linux/arm64 public.ecr.aws/docker/library/node:22-slim AS node-source

# --- Stage 2: Final image ---
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Copy Node.js from the official image (no curl|bash supply chain risk).
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/include/node /usr/local/include/node
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# Only git needed at runtime (no curl in final image — reduced attack surface).
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI — version pinned to match claude-agent-sdk 0.2.91.
RUN npm install -g @anthropic-ai/claude-code@2.1.165 \
    && claude --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py sandbox_client.py path_security.py .

# Copy shared security module (from repo root's shared/ directory)
COPY shared_libs/shared/ /app/shared_libs/shared/

# Bedrock config for the Claude Agent SDK (model overridable at runtime via env).
# The Claude Code CLI refuses --dangerously-skip-permissions (bypassPermissions) as root,
# so run as a non-root user with a writable HOME and work dir.
ENV CLAUDE_CODE_USE_BEDROCK=1 \
    AWS_REGION=us-east-1 \
    BEDROCK_MODEL=us.anthropic.claude-opus-4-8 \
    WORK_DIR=/work \
    HOME=/home/agent

RUN useradd -m -u 1000 -d /home/agent agent \
    && mkdir -p /work /home/agent/.claude \
    && chown -R agent:agent /work /home/agent
USER agent

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')" || exit 1

EXPOSE 8080
CMD ["python", "app.py"]
