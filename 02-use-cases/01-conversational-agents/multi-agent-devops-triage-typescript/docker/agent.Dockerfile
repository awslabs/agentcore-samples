# One image for all three agents plus the mock tool (build context = repo
# root). The workspace to run is selected at RUNTIME via the AGENT_DIR env
# var — a single build serves every service in docker-compose.yaml.
#
# AgentCore Runtime requires linux/arm64 containers:
#   docker build --platform linux/arm64 -f docker/agent.Dockerfile -t sample-agents .
#   docker run -e AGENT_DIR=agents/log-analyst -p 9000:9000 sample-agents
FROM --platform=linux/arm64 node:20-bookworm-slim AS build

WORKDIR /app

# Install the full workspace: npm resolves the Claude Agent SDK's
# platform-specific optionalDependency (the bundled native CLI binary) for
# the *container's* platform (linux-arm64) here — this is why the install
# must happen inside the image and node_modules is never copied from the host.
COPY package.json package-lock.json tsconfig.base.json tsconfig.json ./
COPY packages ./packages
COPY agents ./agents
COPY scripts ./scripts
COPY tests ./tests
RUN npm ci --no-audit --no-fund

RUN npx tsc --build

FROM --platform=linux/arm64 node:20-bookworm-slim

ENV NODE_ENV=production
# serveA2A binds 0.0.0.0 only inside containers (loopback otherwise). Its
# detection checks /.dockerenv or this var; /.dockerenv doesn't exist under
# podman or AgentCore Runtime, so declare container-ness explicitly.
ENV DOCKER_CONTAINER=1
WORKDIR /app

COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/packages ./packages
COPY --from=build /app/agents ./agents
COPY --from=build /app/scripts ./scripts
COPY --from=build /app/package.json ./package.json

# AGENT_DIR selects the workspace at runtime, e.g. agents/lead
CMD ["sh", "-c", "exec npm run start --workspace \"$AGENT_DIR\""]
